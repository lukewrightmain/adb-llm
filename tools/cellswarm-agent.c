/*
 * cellswarm-agent — tiny process management daemon for cellswarm phones.
 *
 * Runs on each phone, listens on port 8082. The cellswarm-master uses
 * this agent to start/stop workers and orchestrate ring relaunches
 * without needing ADB or an external control server.
 *
 * Endpoints:
 *   GET  /status  — running processes + available models
 *   POST /stop    — kill all cellswarm processes
 *   POST /start   — start a cellswarm binary with given args
 *   OPTIONS *     — CORS preflight
 *
 * Usage:  cellswarm-agent [--port 8082]
 * Build:  Cross-compile for ARM64 Android (see build_cellswarm.sh)
 *
 * No dependencies beyond POSIX.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <signal.h>
#include <errno.h>
#include <arpa/inet.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <netinet/in.h>
#include <dirent.h>

#define AGENT_PORT  8082
#define BIN_DIR     "/data/local/tmp/cellswarm/bin"
#define MODEL_DIR   "/data/local/tmp/cellswarm/models"
#define LOG_DIR     "/data/local/tmp"
#define REQ_BUF     8192
#define RESP_BUF    4096

/* ---- minimal JSON helpers (read-only, no library) ---- */

/* Extract string value for "key":"value". Returns length, 0 if not found. */
static int jstr(const char *json, const char *key, char *out, int max) {
    char pat[64];
    snprintf(pat, sizeof(pat), "\"%s\":\"", key);
    const char *s = strstr(json, pat);
    if (!s) { out[0] = 0; return 0; }
    s += strlen(pat);
    const char *e = strchr(s, '"');
    if (!e) { out[0] = 0; return 0; }
    int len = (int)(e - s);
    if (len >= max) len = max - 1;
    memcpy(out, s, len);
    out[len] = 0;
    return len;
}

/* Extract integer value for "key":N. Returns 0 if not found. */
static int jint(const char *json, const char *key) {
    char pat[64];
    snprintf(pat, sizeof(pat), "\"%s\":", key);
    const char *s = strstr(json, pat);
    if (!s) return 0;
    return atoi(s + strlen(pat));
}

/* ---- HTTP helpers ---- */

static void send_resp(int fd, int code, const char *body) {
    char hdr[512];
    int blen = body ? (int)strlen(body) : 0;
    int n = snprintf(hdr, sizeof(hdr),
        "HTTP/1.0 %d OK\r\n"
        "Content-Type: application/json\r\n"
        "Content-Length: %d\r\n"
        "Access-Control-Allow-Origin: *\r\n"
        "Access-Control-Allow-Methods: GET, POST, OPTIONS\r\n"
        "Access-Control-Allow-Headers: Content-Type\r\n"
        "Connection: close\r\n\r\n", code, blen);
    write(fd, hdr, n);
    if (body && blen > 0) write(fd, body, blen);
}

/* ---- PID lookup via /proc ---- */

static int find_pid(const char *name) {
    DIR *d = opendir("/proc");
    if (!d) return 0;
    struct dirent *ent;
    while ((ent = readdir(d))) {
        if (ent->d_name[0] < '1' || ent->d_name[0] > '9') continue;
        char path[128], buf[1024];
        snprintf(path, sizeof(path), "/proc/%s/cmdline", ent->d_name);
        FILE *f = fopen(path, "r");
        if (!f) continue;
        int r = (int)fread(buf, 1, sizeof(buf) - 1, f);
        fclose(f);
        if (r <= 0) continue;
        buf[r] = 0;
        /* cmdline has NUL-separated args; check first arg (binary path) */
        const char *base = strrchr(buf, '/');
        base = base ? base + 1 : buf;
        if (strcmp(base, name) == 0) {
            int pid = atoi(ent->d_name);
            closedir(d);
            return pid;
        }
    }
    closedir(d);
    return 0;
}

/* ---- Handlers ---- */

static void handle_status(int fd) {
    int wpid = find_pid("cellswarm-worker");
    int mpid = find_pid("cellswarm-master");

    /* Scan models */
    char models[2048];
    int pos = 0;
    pos += snprintf(models + pos, sizeof(models) - pos, "[");
    DIR *d = opendir(MODEL_DIR);
    int first = 1;
    if (d) {
        struct dirent *ent;
        while ((ent = readdir(d))) {
            if (!strstr(ent->d_name, ".gguf")) continue;
            char path[512];
            snprintf(path, sizeof(path), "%s/%s", MODEL_DIR, ent->d_name);
            struct stat st;
            if (stat(path, &st) != 0) continue;
            if (!first) pos += snprintf(models + pos, sizeof(models) - pos, ",");
            pos += snprintf(models + pos, sizeof(models) - pos,
                "{\"name\":\"%s\",\"size\":%lld}", ent->d_name, (long long)st.st_size);
            first = 0;
        }
        closedir(d);
    }
    snprintf(models + pos, sizeof(models) - pos, "]");

    char resp[RESP_BUF];
    snprintf(resp, sizeof(resp),
        "{\"worker_pid\":%d,\"master_pid\":%d,\"models\":%s}",
        wpid, mpid, models);
    send_resp(fd, 200, resp);
}

static void handle_stop(int fd) {
    const char *names[] = {"cellswarm-master", "cellswarm-worker",
                           "cellswarm-worker-spec", NULL};
    char resp[512];
    int pos = snprintf(resp, sizeof(resp), "{\"stopped\":[");
    int first = 1;

    for (int i = 0; names[i]; i++) {
        int pid;
        while ((pid = find_pid(names[i])) > 0) {
            kill(pid, SIGKILL);
            if (!first) pos += snprintf(resp + pos, sizeof(resp) - pos, ",");
            pos += snprintf(resp + pos, sizeof(resp) - pos,
                "\"%s:%d\"", names[i], pid);
            first = 0;
            usleep(50000); /* 50ms for proc table to update */
        }
    }
    snprintf(resp + pos, sizeof(resp) - pos, "]}");
    send_resp(fd, 200, resp);
}

static void handle_stop_workers(int fd) {
    const char *names[] = {"cellswarm-worker", "cellswarm-worker-spec", NULL};
    char resp[512];
    int pos = snprintf(resp, sizeof(resp), "{\"stopped\":[");
    int first = 1;

    for (int i = 0; names[i]; i++) {
        int pid;
        while ((pid = find_pid(names[i])) > 0) {
            kill(pid, SIGKILL);
            if (!first) pos += snprintf(resp + pos, sizeof(resp) - pos, ",");
            pos += snprintf(resp + pos, sizeof(resp) - pos,
                "\"%s:%d\"", names[i], pid);
            first = 0;
            usleep(50000);
        }
    }
    snprintf(resp + pos, sizeof(resp) - pos, "]}");
    send_resp(fd, 200, resp);
}

static void handle_start(int fd, const char *body) {
    char bin[64] = {0}, args[4096] = {0}, env_str[512] = {0}, taskset[16] = {0};
    int delay = 0;

    if (!jstr(body, "bin", bin, sizeof(bin))) {
        send_resp(fd, 400, "{\"error\":\"missing bin\"}");
        return;
    }

    jstr(body, "args", args, sizeof(args));
    jstr(body, "env", env_str, sizeof(env_str));
    jstr(body, "taskset", taskset, sizeof(taskset));
    delay = jint(body, "delay");

    /* Validate binary exists */
    char bin_path[256];
    snprintf(bin_path, sizeof(bin_path), "%s/%s", BIN_DIR, bin);
    if (access(bin_path, X_OK) != 0) {
        send_resp(fd, 400, "{\"error\":\"binary not found or not executable\"}");
        return;
    }

    /* Respond before forking (so HTTP response gets sent) */
    pid_t pid = fork();
    if (pid < 0) {
        send_resp(fd, 500, "{\"error\":\"fork failed\"}");
        return;
    }

    if (pid == 0) {
        /* Child process */
        setsid();
        close(fd);

        if (delay > 0) sleep(delay);

        /* Set environment variables (space-separated KEY=VAL pairs) */
        if (env_str[0]) {
            char *save, *tok;
            for (tok = strtok_r(env_str, " ", &save); tok; tok = strtok_r(NULL, " ", &save)) {
                putenv(strdup(tok));
            }
        }

        /* Build shell command */
        char cmd[8192];
        if (taskset[0]) {
            snprintf(cmd, sizeof(cmd), "taskset %s %s %s > %s/%s.log 2>&1",
                     taskset, bin_path, args, LOG_DIR, bin);
        } else {
            snprintf(cmd, sizeof(cmd), "%s %s > %s/%s.log 2>&1",
                     bin_path, args, LOG_DIR, bin);
        }

        execl("/system/bin/sh", "sh", "-c", cmd, NULL);
        _exit(1);
    }

    /* Parent: send response */
    char resp[64];
    snprintf(resp, sizeof(resp), "{\"pid\":%d}", (int)pid);
    send_resp(fd, 200, resp);
}

/* ---- Main request dispatcher ---- */

static void handle_request(int fd) {
    char req[REQ_BUF];
    int total = 0;

    /* Read request (headers + body) */
    while (total < (int)sizeof(req) - 1) {
        int n = read(fd, req + total, sizeof(req) - 1 - total);
        if (n <= 0) break;
        total += n;
        req[total] = 0;
        /* Check if we have full headers */
        char *hdr_end = strstr(req, "\r\n\r\n");
        if (hdr_end) {
            /* Check if we have full body */
            char *cl = strcasestr(req, "content-length:");
            if (cl) {
                int clen = atoi(cl + 15);
                int body_start = (int)(hdr_end + 4 - req);
                int body_have = total - body_start;
                if (body_have >= clen) break;
            } else {
                break; /* No body expected */
            }
        }
    }
    if (total <= 0) return;

    /* Parse method and path */
    char method[8] = {0}, path[64] = {0};
    sscanf(req, "%7s %63s", method, path);

    /* Find body */
    char *body = strstr(req, "\r\n\r\n");
    if (body) body += 4;

    /* Route */
    if (strcmp(method, "OPTIONS") == 0) {
        send_resp(fd, 204, NULL);
    } else if (strcmp(method, "GET") == 0 && strcmp(path, "/status") == 0) {
        handle_status(fd);
    } else if (strcmp(method, "GET") == 0 && strcmp(path, "/") == 0) {
        send_resp(fd, 200, "{\"ok\":true}");
    } else if (strcmp(method, "POST") == 0 && strcmp(path, "/stop") == 0) {
        handle_stop(fd);
    } else if (strcmp(method, "POST") == 0 && strcmp(path, "/stop-workers") == 0) {
        handle_stop_workers(fd);
    } else if (strcmp(method, "POST") == 0 && strcmp(path, "/start") == 0) {
        handle_start(fd, body ? body : "");
    } else {
        send_resp(fd, 404, "{\"error\":\"not found\"}");
    }
}

/* ---- Entry point ---- */

int main(int argc, char **argv) {
    int port = AGENT_PORT;
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--port") == 0 && i + 1 < argc)
            port = atoi(argv[++i]);
    }

    signal(SIGCHLD, SIG_IGN);  /* Auto-reap zombie children */
    signal(SIGPIPE, SIG_IGN);

    int srv = socket(AF_INET, SOCK_STREAM, 0);
    if (srv < 0) { perror("socket"); return 1; }

    int opt = 1;
    setsockopt(srv, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));

    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_port = htons(port);
    addr.sin_addr.s_addr = INADDR_ANY;

    if (bind(srv, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        perror("bind");
        return 1;
    }
    if (listen(srv, 8) < 0) {
        perror("listen");
        return 1;
    }

    fprintf(stderr, "cellswarm-agent listening on :%d\n", port);

    while (1) {
        struct sockaddr_in cli;
        socklen_t cli_len = sizeof(cli);
        int client = accept(srv, (struct sockaddr *)&cli, &cli_len);
        if (client < 0) {
            if (errno == EINTR) continue;
            perror("accept");
            continue;
        }

        /* Set read timeout */
        struct timeval tv = {5, 0};
        setsockopt(client, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));

        handle_request(client);
        close(client);
    }

    return 0;
}
