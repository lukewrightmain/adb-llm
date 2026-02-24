/**
 * IP Range Parser
 *
 * Supports:
 *   10.105.0.12              — single IP
 *   10.105.0.12:5555         — single IP with port
 *   10.105.0.12-48           — last octet range
 *   10.105.0.12-10.105.0.48  — full IP-to-IP range
 *   10.105.0.0/24            — CIDR notation
 *   10.0-1.0-255.1           — multi-octet ranges (any octet)
 *   comma/space/newline separated lists of any of the above
 */

export interface IpEntry {
	host: string;
	port: number;
}

/**
 * Convert an IP address to a 32-bit integer.
 */
function ipToInt(ip: string): number {
	const parts = ip.split('.').map(Number);
	return ((parts[0] << 24) | (parts[1] << 16) | (parts[2] << 8) | parts[3]) >>> 0;
}

/**
 * Convert a 32-bit integer back to an IP string.
 */
function intToIp(n: number): string {
	return [
		(n >>> 24) & 0xff,
		(n >>> 16) & 0xff,
		(n >>> 8) & 0xff,
		n & 0xff,
	].join('.');
}

/**
 * Parse a single token into IP entries.
 */
function parseToken(token: string, defaultPort: number): IpEntry[] {
	const results: IpEntry[] = [];
	let port = defaultPort;

	// Strip trailing port: 10.105.0.12:5555 or 10.105.0.0/24:5555
	const portMatch = token.match(/:(\d+)$/);
	if (portMatch) {
		port = parseInt(portMatch[1], 10);
		token = token.slice(0, -portMatch[0].length);
	}

	// ─── CIDR: 10.105.0.0/24 ────────────────────────────────────────
	const cidrMatch = token.match(/^(\d+\.\d+\.\d+\.\d+)\/(\d+)$/);
	if (cidrMatch) {
		const base = ipToInt(cidrMatch[1]);
		const prefix = parseInt(cidrMatch[2], 10);
		if (prefix < 0 || prefix > 32) return results;
		const mask = prefix === 0 ? 0 : (~0 << (32 - prefix)) >>> 0;
		const network = (base & mask) >>> 0;
		const hostBits = 32 - prefix;
		const count = 1 << hostBits;
		// Skip network (.0) and broadcast (.255) for /24 and larger
		const start = hostBits > 1 ? 1 : 0;
		const end = hostBits > 1 ? count - 1 : count;
		for (let i = start; i < end; i++) {
			results.push({ host: intToIp((network + i) >>> 0), port });
		}
		return results;
	}

	// ─── Full IP-to-IP range: 10.105.0.12-10.105.0.48 ───────────────
	const fullRangeMatch = token.match(/^(\d+\.\d+\.\d+\.\d+)-(\d+\.\d+\.\d+\.\d+)$/);
	if (fullRangeMatch) {
		const startIp = ipToInt(fullRangeMatch[1]);
		const endIp = ipToInt(fullRangeMatch[2]);
		const low = Math.min(startIp, endIp);
		const high = Math.max(startIp, endIp);
		// Sanity cap: max 65536 IPs per range
		const count = high - low + 1;
		if (count > 65536) return results;
		for (let i = low; i <= high; i++) {
			results.push({ host: intToIp(i >>> 0), port });
		}
		return results;
	}

	// ─── Multi-octet range: 10.0-1.0-255.1 ──────────────────────────
	// Each octet can be a single number or a range (start-end)
	const octets = token.split('.');
	if (octets.length === 4) {
		const ranges: Array<[number, number]> = [];
		let isRange = false;

		for (const octet of octets) {
			const rangeMatch = octet.match(/^(\d+)-(\d+)$/);
			if (rangeMatch) {
				const a = parseInt(rangeMatch[1], 10);
				const b = parseInt(rangeMatch[2], 10);
				ranges.push([Math.min(a, b), Math.max(a, b)]);
				isRange = true;
			} else {
				const val = parseInt(octet, 10);
				if (isNaN(val) || val < 0 || val > 255) return results;
				ranges.push([val, val]);
			}
		}

		if (isRange) {
			// Sanity check: don't generate more than 65536 IPs
			let total = 1;
			for (const [lo, hi] of ranges) {
				total *= (hi - lo + 1);
				if (total > 65536) return results;
			}

			for (let a = ranges[0][0]; a <= ranges[0][1]; a++) {
				for (let b = ranges[1][0]; b <= ranges[1][1]; b++) {
					for (let c = ranges[2][0]; c <= ranges[2][1]; c++) {
						for (let d = ranges[3][0]; d <= ranges[3][1]; d++) {
							results.push({ host: `${a}.${b}.${c}.${d}`, port });
						}
					}
				}
			}
			return results;
		}

		// ─── Plain IP: 10.105.0.12 ──────────────────────────────────
		const ip = octets.map(Number);
		if (ip.every(n => n >= 0 && n <= 255)) {
			results.push({ host: token, port });
			return results;
		}
	}

	return results;
}

/**
 * Parse user input into a list of IP:port entries.
 *
 * Accepts any combination of formats, separated by commas, spaces, or newlines.
 * Returns deduplicated list.
 */
export function parseIpRange(input: string, defaultPort: number = 5555): IpEntry[] {
	const tokens = input.split(/[,\n\s]+/).map(s => s.trim()).filter(Boolean);
	const results: IpEntry[] = [];
	const seen = new Set<string>();

	for (const token of tokens) {
		const entries = parseToken(token, defaultPort);
		for (const entry of entries) {
			const key = `${entry.host}:${entry.port}`;
			if (!seen.has(key)) {
				seen.add(key);
				results.push(entry);
			}
		}
	}

	return results;
}

/**
 * Describe the parsed range for display (e.g. "256 IPs in 10.105.0.0/24").
 */
export function describeIpInput(input: string, defaultPort: number = 5555): string {
	const entries = parseIpRange(input, defaultPort);
	if (entries.length === 0) return 'No valid IPs';
	if (entries.length === 1) return entries[0].host;
	return `${entries.length} IPs`;
}
