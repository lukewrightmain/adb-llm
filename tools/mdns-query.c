/* mdns-query — send an mDNS query and print the response.
 * Usage: mdns-query <hostname>
 * Example: mdns-query cellswarm
 */
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <arpa/inet.h>
#include <sys/socket.h>
#include <netinet/in.h>

int main(int argc, char *argv[]) {
    if (argc < 2) { fprintf(stderr, "Usage: %s <hostname>\n", argv[0]); return 1; }
    const char *hostname = argv[1];

    /* Build query name: hostname.local */
    unsigned char name[256];
    int nlen = 0;
    int hlen = strlen(hostname);
    name[nlen++] = (unsigned char)hlen;
    memcpy(name + nlen, hostname, hlen); nlen += hlen;
    name[nlen++] = 5;
    memcpy(name + nlen, "local", 5); nlen += 5;
    name[nlen++] = 0;

    /* Build query packet */
    unsigned char pkt[512];
    memset(pkt, 0, 12);
    pkt[0] = 0; pkt[1] = 1; /* txid */
    pkt[5] = 1;              /* qdcount = 1 */
    int off = 12;
    memcpy(pkt + off, name, nlen); off += nlen;
    pkt[off++] = 0; pkt[off++] = 1; /* type A */
    pkt[off++] = 0; pkt[off++] = 1; /* class IN */

    int sock = socket(AF_INET, SOCK_DGRAM, 0);
    int reuse = 1;
    setsockopt(sock, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse));

    struct sockaddr_in bind_addr = {0};
    bind_addr.sin_family = AF_INET;
    bind_addr.sin_port = htons(5353);
    bind_addr.sin_addr.s_addr = INADDR_ANY;
    bind(sock, (struct sockaddr *)&bind_addr, sizeof(bind_addr));

    struct ip_mreq mreq;
    inet_pton(AF_INET, "224.0.0.251", &mreq.imr_multiaddr);
    mreq.imr_interface.s_addr = INADDR_ANY;
    setsockopt(sock, IPPROTO_IP, IP_ADD_MEMBERSHIP, &mreq, sizeof(mreq));

    struct sockaddr_in dest = {0};
    dest.sin_family = AF_INET;
    dest.sin_port = htons(5353);
    inet_pton(AF_INET, "224.0.0.251", &dest.sin_addr);
    sendto(sock, pkt, off, 0, (struct sockaddr *)&dest, sizeof(dest));
    printf("Sent mDNS query for %s.local\n", hostname);

    /* Wait for response */
    struct timeval tv = {5, 0};
    setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));

    unsigned char buf[512];
    struct sockaddr_in src;
    socklen_t slen = sizeof(src);
    while (1) {
        int n = recvfrom(sock, buf, sizeof(buf), 0, (struct sockaddr *)&src, &slen);
        if (n < 0) { printf("TIMEOUT - no response\n"); break; }
        if (buf[2] & 0x80) { /* response */
            int ancount = (buf[6] << 8) | buf[7];
            printf("Response from %s, answers=%d\n", inet_ntoa(src.sin_addr), ancount);
            if (ancount > 0 && n >= 4) {
                unsigned char *ip = buf + n - 4;
                printf("  %s.local -> %d.%d.%d.%d\n", hostname, ip[0], ip[1], ip[2], ip[3]);
            }
            break;
        }
    }
    close(sock);
    return 0;
}
