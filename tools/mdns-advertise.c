/*
 * mdns-advertise — tiny mDNS responder for a single hostname.
 *
 * Joins the mDNS multicast group (224.0.0.251:5353), listens for A-record
 * queries matching "<hostname>.local", and responds with the given IP.
 *
 * Usage:  mdns-advertise <hostname> <ip>
 * Example: mdns-advertise cellswarm 10.105.0.36
 *
 * Then:   ping cellswarm.local   → 10.105.0.36
 *         curl http://cellswarm.local:8080/
 *
 * Designed to be cross-compiled for ARM64 Android and run on phones.
 * No dependencies beyond POSIX sockets.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <signal.h>
#include <errno.h>
#include <arpa/inet.h>
#include <sys/socket.h>
#include <netinet/in.h>

#define MDNS_PORT 5353
#define MDNS_ADDR "224.0.0.251"
#define BUF_SIZE  512
#define TTL_SECS  120

static volatile int running = 1;

static void handle_signal(int sig) {
    (void)sig;
    running = 0;
}

/* Encode a DNS name label: "cellswarm.local" → \x09cellswarm\x05local\x00 */
static int encode_dns_name(const char *hostname, unsigned char *out, int max_len) {
    char fqdn[256];
    snprintf(fqdn, sizeof(fqdn), "%s.local", hostname);

    int pos = 0;
    const char *p = fqdn;
    while (*p && pos < max_len - 2) {
        const char *dot = strchr(p, '.');
        int label_len = dot ? (int)(dot - p) : (int)strlen(p);
        if (pos + 1 + label_len >= max_len) break;
        out[pos++] = (unsigned char)label_len;
        memcpy(out + pos, p, label_len);
        pos += label_len;
        p += label_len;
        if (*p == '.') p++;
    }
    out[pos++] = 0; /* root label */
    return pos;
}

/* Check if a DNS query packet contains a question for our hostname (type A) */
static int match_query(const unsigned char *pkt, int pkt_len,
                       const unsigned char *our_name, int name_len) {
    if (pkt_len < 12) return 0;

    /* Flags: must be a standard query (QR=0, OPCODE=0) */
    unsigned short flags = (pkt[2] << 8) | pkt[3];
    if (flags & 0x8000) return 0; /* QR=1 means response, skip */

    unsigned short qdcount = (pkt[4] << 8) | pkt[5];
    if (qdcount == 0) return 0;

    /* Walk question section */
    int off = 12;
    for (int q = 0; q < qdcount && off < pkt_len; q++) {
        /* Compare QNAME with our name (case-insensitive) */
        int name_start = off;

        /* Walk labels to find end of QNAME */
        int qname_end = off;
        while (qname_end < pkt_len && pkt[qname_end] != 0) {
            if ((pkt[qname_end] & 0xC0) == 0xC0) {
                qname_end += 2;
                goto check_type;
            }
            qname_end += 1 + pkt[qname_end];
        }
        qname_end++; /* skip the 0 terminator */

    check_type:
        if (qname_end + 4 > pkt_len) return 0;

        unsigned short qtype  = (pkt[qname_end] << 8) | pkt[qname_end + 1];
        unsigned short qclass = (pkt[qname_end + 2] << 8) | pkt[qname_end + 3];

        /* Type A (1) or ANY (255), class IN (1) or ANY (255) */
        if ((qtype == 1 || qtype == 255) && ((qclass & 0x7FFF) == 1 || (qclass & 0x7FFF) == 255)) {
            /* Compare name (simple: byte-compare with case folding) */
            int qname_len = qname_end - name_start;
            if (qname_len == name_len) {
                int match = 1;
                for (int i = 0; i < name_len && match; i++) {
                    unsigned char a = pkt[name_start + i];
                    unsigned char b = our_name[i];
                    /* Case-insensitive for ASCII letters */
                    if (a >= 'A' && a <= 'Z') a += 32;
                    if (b >= 'A' && b <= 'Z') b += 32;
                    if (a != b) match = 0;
                }
                if (match) return 1;
            }
        }

        off = qname_end + 4;
    }
    return 0;
}

/* Build an mDNS response packet for an A record */
static int build_response(const unsigned char *query, int query_len,
                          const unsigned char *name, int name_len,
                          uint32_t ip_addr,
                          unsigned char *resp, int max_len) {
    (void)query_len;
    if (max_len < 12 + name_len + 10 + 4) return -1;

    /* Copy transaction ID from query */
    resp[0] = query[0];
    resp[1] = query[1];

    /* Flags: QR=1 (response), AA=1 (authoritative) */
    resp[2] = 0x84;
    resp[3] = 0x00;

    /* QDCOUNT=0, ANCOUNT=1, NSCOUNT=0, ARCOUNT=0 */
    resp[4] = 0; resp[5] = 0;
    resp[6] = 0; resp[7] = 1;
    resp[8] = 0; resp[9] = 0;
    resp[10] = 0; resp[11] = 0;

    int off = 12;

    /* Answer: NAME */
    memcpy(resp + off, name, name_len);
    off += name_len;

    /* TYPE = A (1) */
    resp[off++] = 0x00;
    resp[off++] = 0x01;

    /* CLASS = IN (1) with cache-flush bit set */
    resp[off++] = 0x80;
    resp[off++] = 0x01;

    /* TTL */
    resp[off++] = (TTL_SECS >> 24) & 0xFF;
    resp[off++] = (TTL_SECS >> 16) & 0xFF;
    resp[off++] = (TTL_SECS >> 8) & 0xFF;
    resp[off++] = TTL_SECS & 0xFF;

    /* RDLENGTH = 4 (IPv4) */
    resp[off++] = 0x00;
    resp[off++] = 0x04;

    /* RDATA = IP address (network byte order) */
    memcpy(resp + off, &ip_addr, 4);
    off += 4;

    return off;
}

int main(int argc, char *argv[]) {
    if (argc < 3) {
        fprintf(stderr, "Usage: %s <hostname> <ip> [bind_ip]\n", argv[0]);
        fprintf(stderr, "  <ip>      = IP to advertise in A record\n");
        fprintf(stderr, "  [bind_ip] = local interface for multicast (default: <ip>)\n");
        fprintf(stderr, "Example: %s cellswarm 10.105.0.36\n", argv[0]);
        fprintf(stderr, "Example: %s cellswarm 10.105.0.41 10.69.1.235  (cross-VLAN relay)\n", argv[0]);
        return 1;
    }

    const char *hostname = argv[1];
    const char *ip_str = argv[2];
    const char *bind_str = argc >= 4 ? argv[3] : ip_str;

    uint32_t ip_addr;
    if (inet_pton(AF_INET, ip_str, &ip_addr) != 1) {
        fprintf(stderr, "Invalid advertise IP: %s\n", ip_str);
        return 1;
    }

    uint32_t bind_addr_ip;
    if (inet_pton(AF_INET, bind_str, &bind_addr_ip) != 1) {
        fprintf(stderr, "Invalid bind IP: %s\n", bind_str);
        return 1;
    }

    /* Encode our DNS name */
    unsigned char our_name[256];
    int name_len = encode_dns_name(hostname, our_name, sizeof(our_name));

    /* Create UDP socket */
    int sock = socket(AF_INET, SOCK_DGRAM, 0);
    if (sock < 0) {
        perror("socket");
        return 1;
    }

    int reuse = 1;
    setsockopt(sock, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse));
#ifdef SO_REUSEPORT
    setsockopt(sock, SOL_SOCKET, SO_REUSEPORT, &reuse, sizeof(reuse));
#endif

    struct sockaddr_in bind_addr;
    memset(&bind_addr, 0, sizeof(bind_addr));
    bind_addr.sin_family = AF_INET;
    bind_addr.sin_port = htons(MDNS_PORT);
    bind_addr.sin_addr.s_addr = INADDR_ANY;

    if (bind(sock, (struct sockaddr *)&bind_addr, sizeof(bind_addr)) < 0) {
        perror("bind");
        close(sock);
        return 1;
    }

    /* Join multicast group on the bind interface */
    struct ip_mreq mreq;
    inet_pton(AF_INET, MDNS_ADDR, &mreq.imr_multiaddr);
    mreq.imr_interface.s_addr = bind_addr_ip;
    if (setsockopt(sock, IPPROTO_IP, IP_ADD_MEMBERSHIP, &mreq, sizeof(mreq)) < 0) {
        perror("IP_ADD_MEMBERSHIP");
        close(sock);
        return 1;
    }

    /* Set outgoing multicast interface */
    struct in_addr mc_iface;
    mc_iface.s_addr = bind_addr_ip;
    setsockopt(sock, IPPROTO_IP, IP_MULTICAST_IF, &mc_iface, sizeof(mc_iface));

    /* Set multicast TTL */
    unsigned char mc_ttl = 255;
    setsockopt(sock, IPPROTO_IP, IP_MULTICAST_TTL, &mc_ttl, sizeof(mc_ttl));

    signal(SIGINT, handle_signal);
    signal(SIGTERM, handle_signal);

    fprintf(stderr, "mdns-advertise: %s.local → %s (iface %s, pid %d)\n", hostname, ip_str, bind_str, getpid());

    /* Send an initial unsolicited announcement */
    {
        unsigned char ann[BUF_SIZE];
        /* Fake a query packet for build_response's txid copy */
        unsigned char fake_query[2] = {0, 0};
        int ann_len = build_response(fake_query, 2, our_name, name_len, ip_addr, ann, sizeof(ann));
        if (ann_len > 0) {
            struct sockaddr_in mc_dest;
            memset(&mc_dest, 0, sizeof(mc_dest));
            mc_dest.sin_family = AF_INET;
            mc_dest.sin_port = htons(MDNS_PORT);
            inet_pton(AF_INET, MDNS_ADDR, &mc_dest.sin_addr);
            sendto(sock, ann, ann_len, 0, (struct sockaddr *)&mc_dest, sizeof(mc_dest));
        }
    }

    unsigned char buf[BUF_SIZE];
    unsigned char resp[BUF_SIZE];

    while (running) {
        struct sockaddr_in src;
        socklen_t src_len = sizeof(src);
        int n = recvfrom(sock, buf, sizeof(buf), 0, (struct sockaddr *)&src, &src_len);
        if (n < 0) {
            if (errno == EINTR) continue;
            break;
        }

        if (match_query(buf, n, our_name, name_len)) {
            int resp_len = build_response(buf, n, our_name, name_len, ip_addr, resp, sizeof(resp));
            if (resp_len > 0) {
                /* Send response to multicast group (mDNS standard) */
                struct sockaddr_in mc_dest;
                memset(&mc_dest, 0, sizeof(mc_dest));
                mc_dest.sin_family = AF_INET;
                mc_dest.sin_port = htons(MDNS_PORT);
                inet_pton(AF_INET, MDNS_ADDR, &mc_dest.sin_addr);
                sendto(sock, resp, resp_len, 0, (struct sockaddr *)&mc_dest, sizeof(mc_dest));
            }
        }
    }

    fprintf(stderr, "mdns-advertise: shutting down\n");
    close(sock);
    return 0;
}
