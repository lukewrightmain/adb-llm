/*
 * tcp_hop_bench.c — Standalone raw TCP hop latency benchmark
 *
 * Measures the time to send/recv a 28.7KB payload (same size as one ring hop
 * activation: hidden_dim=7168 * 4 bytes FP32 + 24-byte header) over a raw
 * TCP socket. This gives us the theoretical minimum hop time without ZMQ.
 *
 * Usage:
 *   # On receiving phone (server mode):
 *   tcp_hop_bench -s -p 9000
 *
 *   # On sending phone (client mode):
 *   tcp_hop_bench -c <server_ip> -p 9000 [-n 1000] [-b 28696]
 *
 * Modes:
 *   -s          Server mode (listen, recv, send ACK)
 *   -c <ip>     Client mode (connect, send payload, recv ACK)
 *   -p <port>   Port (default 9000)
 *   -n <count>  Number of round trips (default 1000)
 *   -b <bytes>  Payload size in bytes (default 28696 = 7168*4 + 24)
 *   -r          Ring mode: recv from prev, forward to next (like a ring hop)
 *               Use: -r <prev_port> <next_ip> <next_port>
 *
 * Build for ARM64 (Android):
 *   $NDK/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android34-clang \
 *     -O2 -o tcp_hop_bench tcp_hop_bench.c -static
 *
 * Build for x86_64 (host):
 *   gcc -O2 -o tcp_hop_bench tcp_hop_bench.c
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <errno.h>
#include <time.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <arpa/inet.h>

#define DEFAULT_PORT    9000
#define DEFAULT_COUNT   1000
#define DEFAULT_PAYLOAD (7168 * 4 + 24)  /* 28696 bytes — one ring hop activation */

static int64_t now_us(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (int64_t)ts.tv_sec * 1000000LL + ts.tv_nsec / 1000LL;
}

/* Send exactly n bytes */
static int send_all(int fd, const void *buf, size_t n) {
    const char *p = (const char *)buf;
    size_t sent = 0;
    while (sent < n) {
        ssize_t r = write(fd, p + sent, n - sent);
        if (r <= 0) {
            if (r < 0 && errno == EINTR) continue;
            return -1;
        }
        sent += r;
    }
    return 0;
}

/* Recv exactly n bytes */
static int recv_all(int fd, void *buf, size_t n) {
    char *p = (char *)buf;
    size_t got = 0;
    while (got < n) {
        ssize_t r = read(fd, p + got, n - got);
        if (r <= 0) {
            if (r < 0 && errno == EINTR) continue;
            return -1;
        }
        got += r;
    }
    return 0;
}

static int set_tcp_nodelay(int fd) {
    int flag = 1;
    return setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &flag, sizeof(flag));
}

static int create_listener(int port) {
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) { perror("socket"); return -1; }

    int reuse = 1;
    setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse));

    struct sockaddr_in addr = {0};
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = INADDR_ANY;
    addr.sin_port = htons(port);

    if (bind(fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        perror("bind"); close(fd); return -1;
    }
    if (listen(fd, 1) < 0) {
        perror("listen"); close(fd); return -1;
    }
    return fd;
}

static int connect_to(const char *ip, int port) {
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) { perror("socket"); return -1; }

    struct sockaddr_in addr = {0};
    addr.sin_family = AF_INET;
    addr.sin_port = htons(port);
    if (inet_pton(AF_INET, ip, &addr.sin_addr) != 1) {
        fprintf(stderr, "Invalid IP: %s\n", ip);
        close(fd); return -1;
    }

    if (connect(fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        perror("connect"); close(fd); return -1;
    }
    return fd;
}

/* Compare function for qsort */
static int cmp_i64(const void *a, const void *b) {
    int64_t va = *(const int64_t *)a;
    int64_t vb = *(const int64_t *)b;
    return (va > vb) - (va < vb);
}

static void print_stats(const char *label, int64_t *samples, int n) {
    qsort(samples, n, sizeof(int64_t), cmp_i64);

    int64_t sum = 0;
    for (int i = 0; i < n; i++) sum += samples[i];

    double mean = (double)sum / n;
    double p50  = samples[n / 2];
    double p95  = samples[(int)(n * 0.95)];
    double p99  = samples[(int)(n * 0.99)];
    double min  = samples[0];
    double max  = samples[n - 1];

    printf("%s (n=%d):\n", label, n);
    printf("  mean=%.1f us  p50=%.0f us  p95=%.0f us  p99=%.0f us\n",
           mean, p50, p95, p99);
    printf("  min=%.0f us  max=%.0f us\n", min, max);
    printf("  mean=%.3f ms  p50=%.3f ms\n", mean / 1000.0, p50 / 1000.0);
}

/* ─── Client mode: send payload, recv 1-byte ACK ─── */
static int run_client(const char *server_ip, int port, int count, int payload_size) {
    printf("Client: connecting to %s:%d, payload=%d bytes, rounds=%d\n",
           server_ip, port, payload_size, count);

    int fd = connect_to(server_ip, port);
    if (fd < 0) return 1;
    set_tcp_nodelay(fd);

    char *buf = (char *)malloc(payload_size);
    memset(buf, 0xAB, payload_size);  /* fill with pattern */
    char ack;

    /* Warmup — 50 rounds */
    for (int i = 0; i < 50; i++) {
        send_all(fd, buf, payload_size);
        recv_all(fd, &ack, 1);
    }

    int64_t *send_times = (int64_t *)malloc(count * sizeof(int64_t));
    int64_t *rtt_times  = (int64_t *)malloc(count * sizeof(int64_t));

    for (int i = 0; i < count; i++) {
        int64_t t0 = now_us();
        send_all(fd, buf, payload_size);
        int64_t t1 = now_us();
        recv_all(fd, &ack, 1);
        int64_t t2 = now_us();

        send_times[i] = t1 - t0;
        rtt_times[i]  = t2 - t0;
    }

    print_stats("Send (write syscall)", send_times, count);
    printf("\n");
    print_stats("Full RTT (send + remote recv + ACK)", rtt_times, count);

    free(buf);
    free(send_times);
    free(rtt_times);
    close(fd);
    return 0;
}

/* ─── Server mode: recv payload, send 1-byte ACK ─── */
static int run_server(int port, int count, int payload_size) {
    printf("Server: listening on port %d, payload=%d bytes\n", port, payload_size);

    int listen_fd = create_listener(port);
    if (listen_fd < 0) return 1;

    printf("Waiting for connection...\n");
    int fd = accept(listen_fd, NULL, NULL);
    if (fd < 0) { perror("accept"); close(listen_fd); return 1; }
    set_tcp_nodelay(fd);
    printf("Connected.\n");

    char *buf = (char *)malloc(payload_size);
    char ack = 'K';

    /* Warmup */
    for (int i = 0; i < 50; i++) {
        recv_all(fd, buf, payload_size);
        send_all(fd, &ack, 1);
    }

    int64_t *recv_times = (int64_t *)malloc(count * sizeof(int64_t));

    for (int i = 0; i < count; i++) {
        int64_t t0 = now_us();
        recv_all(fd, buf, payload_size);
        int64_t t1 = now_us();
        send_all(fd, &ack, 1);

        recv_times[i] = t1 - t0;
    }

    print_stats("Recv (read syscall)", recv_times, count);

    free(buf);
    free(recv_times);
    close(fd);
    close(listen_fd);
    return 0;
}

/* ─── Ring mode: recv from prev rank, forward to next rank ─── */
static int run_ring(int listen_port, const char *next_ip, int next_port,
                    int count, int payload_size) {
    printf("Ring: listen=%d → forward to %s:%d, payload=%d bytes, rounds=%d\n",
           listen_port, next_ip, next_port, payload_size, count);

    int listen_fd = create_listener(listen_port);
    if (listen_fd < 0) return 1;

    /* Connect to next rank first */
    int send_fd = connect_to(next_ip, next_port);
    if (send_fd < 0) { close(listen_fd); return 1; }
    set_tcp_nodelay(send_fd);

    printf("Waiting for prev rank connection...\n");
    int recv_fd = accept(listen_fd, NULL, NULL);
    if (recv_fd < 0) { perror("accept"); close(listen_fd); close(send_fd); return 1; }
    set_tcp_nodelay(recv_fd);
    printf("Connected. Running ring benchmark...\n");

    char *buf = (char *)malloc(payload_size);
    int total = 50 + count;  /* warmup + measured */

    int64_t *recv_times = (int64_t *)malloc(count * sizeof(int64_t));
    int64_t *send_times = (int64_t *)malloc(count * sizeof(int64_t));
    int64_t *hop_times  = (int64_t *)malloc(count * sizeof(int64_t));

    for (int i = 0; i < total; i++) {
        int64_t t0 = now_us();
        recv_all(recv_fd, buf, payload_size);
        int64_t t1 = now_us();
        send_all(send_fd, buf, payload_size);
        int64_t t2 = now_us();

        if (i >= 50) {
            int j = i - 50;
            recv_times[j] = t1 - t0;
            send_times[j] = t2 - t1;
            hop_times[j]  = t2 - t0;
        }
    }

    print_stats("Recv (from prev rank)", recv_times, count);
    printf("\n");
    print_stats("Send (to next rank)", send_times, count);
    printf("\n");
    print_stats("Total hop (recv + forward)", hop_times, count);

    free(buf);
    free(recv_times);
    free(send_times);
    free(hop_times);
    close(recv_fd);
    close(send_fd);
    close(listen_fd);
    return 0;
}

static void usage(const char *prog) {
    fprintf(stderr, "Usage:\n");
    fprintf(stderr, "  %s -s -p <port> [-n <count>] [-b <bytes>]\n", prog);
    fprintf(stderr, "  %s -c <ip> -p <port> [-n <count>] [-b <bytes>]\n", prog);
    fprintf(stderr, "  %s -r <prev_port> <next_ip> <next_port> [-n <count>] [-b <bytes>]\n", prog);
    fprintf(stderr, "\nDefaults: port=%d count=%d bytes=%d\n",
            DEFAULT_PORT, DEFAULT_COUNT, DEFAULT_PAYLOAD);
}

int main(int argc, char **argv) {
    int mode = 0;  /* 0=unset, 1=server, 2=client, 3=ring */
    const char *server_ip = NULL;
    const char *next_ip = NULL;
    int port = DEFAULT_PORT;
    int next_port = 0;
    int listen_port = 0;
    int count = DEFAULT_COUNT;
    int payload_size = DEFAULT_PAYLOAD;

    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "-s") == 0) {
            mode = 1;
        } else if (strcmp(argv[i], "-c") == 0 && i + 1 < argc) {
            mode = 2;
            server_ip = argv[++i];
        } else if (strcmp(argv[i], "-r") == 0 && i + 3 < argc) {
            mode = 3;
            listen_port = atoi(argv[++i]);
            next_ip = argv[++i];
            next_port = atoi(argv[++i]);
        } else if (strcmp(argv[i], "-p") == 0 && i + 1 < argc) {
            port = atoi(argv[++i]);
        } else if (strcmp(argv[i], "-n") == 0 && i + 1 < argc) {
            count = atoi(argv[++i]);
        } else if (strcmp(argv[i], "-b") == 0 && i + 1 < argc) {
            payload_size = atoi(argv[++i]);
        } else {
            usage(argv[0]);
            return 1;
        }
    }

    if (mode == 0) {
        usage(argv[0]);
        return 1;
    }

    switch (mode) {
        case 1: return run_server(port, count, payload_size);
        case 2: return run_client(server_ip, port, count, payload_size);
        case 3: return run_ring(listen_port, next_ip, next_port, count, payload_size);
    }
    return 1;
}
