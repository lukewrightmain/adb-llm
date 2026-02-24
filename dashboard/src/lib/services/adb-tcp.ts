/**
 * ADB over TCP via WebSocket proxy.
 *
 * Connects to ADB daemons on the network through a lightweight WS→TCP proxy.
 * The proxy runs on the same server as the dashboard (ws://host:3002/adb/{ip}:{port}).
 *
 * Implements the same ReadableWritablePair interface that AdbDaemonTransport.authenticate()
 * expects, so TCP devices work identically to USB devices after connection.
 */

import { Adb, AdbDaemonTransport, AdbPacketHeader } from '@yume-chan/adb';
import { EmptyUint8Array, Uint8ArrayExactReadable } from '@yume-chan/struct';
import { credentialStore } from '$lib/services/adb-keys';

/** Default WS proxy port (matches ws-proxy.mjs) */
const DEFAULT_PROXY_PORT = 3002;

/** Stored TCP devices for auto-reconnect */
const TCP_DEVICES_KEY = 'cellswarm-tcp-devices';

export interface TcpDeviceEntry {
	host: string;
	port: number;
	label?: string;
}

/**
 * Get the WebSocket proxy URL for a given ADB target.
 */
function getProxyUrl(targetHost: string, targetPort: number): string {
	// Use same hostname as page, different port
	const wsProtocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
	const proxyPort = DEFAULT_PROXY_PORT;
	return `${wsProtocol}//${location.hostname}:${proxyPort}/adb/${targetHost}:${targetPort}`;
}

/**
 * Create an ADB connection over WebSocket that implements
 * ReadableWritablePair<AdbPacketData, Consumable<AdbPacketInit>>
 */
function createWsConnection(ws: WebSocket) {
	// Buffer incoming binary data — ADB sends 24-byte header then payload
	let incomingBuffer = new Uint8Array(0);
	let readResolve: ((value: any) => void) | null = null;
	let closed = false;
	let readQueue: any[] = [];

	function appendBuffer(data: Uint8Array) {
		const merged = new Uint8Array(incomingBuffer.length + data.length);
		merged.set(incomingBuffer);
		merged.set(data, incomingBuffer.length);
		incomingBuffer = merged;
	}

	function tryParsePacket(): any | null {
		const MAX_SKIP = 256;
		let skipped = 0;

		while (incomingBuffer.length >= 24) {
			let header;
			try {
				const stream = new Uint8ArrayExactReadable(incomingBuffer.slice(0, 24));
				header = AdbPacketHeader.deserialize(stream);
			} catch {
				// Corrupted header — skip 1 byte
				incomingBuffer = incomingBuffer.slice(1);
				skipped++;
				if (skipped >= MAX_SKIP) return null;
				continue;
			}

			// Validate magic
			if (header.magic !== (header.command ^ 0xffffffff)) {
				// Bad data — skip this byte and retry
				incomingBuffer = incomingBuffer.slice(1);
				skipped++;
				if (skipped >= MAX_SKIP) return null;
				continue;
			}

			const totalNeeded = 24 + header.payloadLength;
			if (incomingBuffer.length < totalNeeded) return null;

			let payload: Uint8Array;
			if (header.payloadLength > 0) {
				payload = incomingBuffer.slice(24, totalNeeded);
			} else {
				payload = EmptyUint8Array;
			}

			// Advance buffer
			incomingBuffer = incomingBuffer.slice(totalNeeded);

			return {
				command: header.command,
				arg0: header.arg0,
				arg1: header.arg1,
				payloadLength: header.payloadLength,
				checksum: header.checksum,
				magic: header.magic,
				payload,
			};
		}

		return null;
	}

	function drain() {
		while (readResolve) {
			const packet = tryParsePacket();
			if (!packet) break;
			const resolve = readResolve;
			readResolve = null;
			resolve(packet);
		}

		// Also fill any queued packets
		let packet;
		while ((packet = tryParsePacket())) {
			readQueue.push(packet);
		}
	}

	ws.binaryType = 'arraybuffer';

	ws.addEventListener('message', (event) => {
		appendBuffer(new Uint8Array(event.data));
		drain();
	});

	ws.addEventListener('close', () => {
		closed = true;
		if (readResolve) {
			readResolve(null);
			readResolve = null;
		}
	});

	ws.addEventListener('error', () => {
		closed = true;
		if (readResolve) {
			readResolve(null);
			readResolve = null;
		}
	});

	const readable = new ReadableStream({
		pull(controller) {
			// Check queue first
			if (readQueue.length > 0) {
				controller.enqueue(readQueue.shift());
				return;
			}

			// Check buffer
			const packet = tryParsePacket();
			if (packet) {
				controller.enqueue(packet);
				return;
			}

			if (closed) {
				controller.close();
				return;
			}

			// Wait for data
			return new Promise<void>((resolve) => {
				readResolve = (pkt) => {
					if (pkt) {
						controller.enqueue(pkt);
					} else {
						controller.close();
					}
					resolve();
				};
			});
		},
	});

	const writable = new WritableStream<Consumable<any>>({
		write(chunk) {
			return chunk.tryConsume(async (init: any) => {
				if (ws.readyState !== WebSocket.OPEN) {
					throw new Error('WebSocket closed');
				}

				// Serialize ADB packet header (24 bytes, little-endian)
				const header = new ArrayBuffer(24);
				const view = new DataView(header);
				view.setUint32(0, init.command, true);
				view.setUint32(4, init.arg0, true);
				view.setUint32(8, init.arg1, true);
				view.setUint32(12, init.payload?.length ?? 0, true);
				view.setUint32(16, init.checksum ?? 0, true);
				view.setInt32(20, init.magic ?? 0, true);

				ws.send(new Uint8Array(header));

				if (init.payload?.length > 0) {
					ws.send(init.payload);
				}
			});
		},
		close() {
			ws.close();
		},
	});

	return { readable, writable };
}

/**
 * Connect to an ADB daemon over TCP via the WebSocket proxy.
 * Returns a connected Adb instance, just like connectDevice() does for USB.
 */
export async function connectTcpDevice(host: string, port: number = 5555): Promise<Adb> {
	const wsUrl = getProxyUrl(host, port);
	const serial = `${host}:${port}`;

	console.log(`[adb-tcp] Connecting to ${serial} via ${wsUrl}`);

	// Open WebSocket to proxy
	const ws = await new Promise<WebSocket>((resolve, reject) => {
		const socket = new WebSocket(wsUrl);
		socket.binaryType = 'arraybuffer';

		const timeout = setTimeout(() => {
			socket.close();
			reject(new Error(`Connection timeout to ${serial}`));
		}, 10000);

		socket.addEventListener('open', () => {
			clearTimeout(timeout);
			console.log(`[adb-tcp] WebSocket open to ${serial}`);
			resolve(socket);
		});

		socket.addEventListener('error', () => {
			clearTimeout(timeout);
			reject(new Error(`Failed to connect to ${serial} via proxy`));
		});
	});

	const connection = createWsConnection(ws);

	// Authenticate with timeout — auth can hang if phone never responds
	console.log(`[adb-tcp] Starting ADB authentication for ${serial}...`);
	const authPromise = AdbDaemonTransport.authenticate({
		serial,
		connection,
		credentialStore,
	});

	const timeoutPromise = new Promise<never>((_, reject) => {
		setTimeout(() => {
			ws.close();
			reject(new Error(`ADB authentication timeout for ${serial} (30s). Phone may not trust any imported keys — check "Always allow" on device.`));
		}, 30000);
	});

	const transport = await Promise.race([authPromise, timeoutPromise]);
	console.log(`[adb-tcp] Authenticated ${serial} successfully`);

	return new Adb(transport);
}

/**
 * Check if the WS proxy is reachable.
 */
export async function isProxyAvailable(): Promise<boolean> {
	try {
		const ws = new WebSocket(getScanUrl());
		return new Promise((resolve) => {
			const timeout = setTimeout(() => {
				ws.close();
				resolve(false);
			}, 2000);

			ws.addEventListener('open', () => {
				clearTimeout(timeout);
				ws.close();
				resolve(true);
			});
			ws.addEventListener('error', () => {
				clearTimeout(timeout);
				resolve(false);
			});
		});
	} catch {
		return false;
	}
}

/**
 * Get the WebSocket URL for the scan endpoint.
 */
function getScanUrl(): string {
	const wsProtocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
	return `${wsProtocol}//${location.hostname}:${DEFAULT_PROXY_PORT}/scan`;
}

export interface ScanProgress {
	type: 'start' | 'found' | 'progress' | 'done' | 'error';
	total?: number;
	scanned?: number;
	found?: number;
	ip?: string;
	port?: number;
	elapsedMs?: number;
	error?: string;
}

/**
 * Scan a list of IPs for ADB daemons via the WS proxy.
 * Calls onProgress for real-time updates as devices are discovered.
 * Returns list of found IPs.
 */
export async function scanNetwork(
	ips: string[],
	port: number = 5555,
	onProgress?: (event: ScanProgress) => void,
	signal?: AbortSignal,
): Promise<string[]> {
	const wsUrl = getScanUrl();
	const found: string[] = [];

	return new Promise((resolve, reject) => {
		const ws = new WebSocket(wsUrl);

		const cleanup = () => {
			if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
				ws.close();
			}
		};

		if (signal) {
			signal.addEventListener('abort', () => {
				cleanup();
				reject(new DOMException('Scan aborted', 'AbortError'));
			});
		}

		ws.addEventListener('open', () => {
			ws.send(JSON.stringify({ ips, port, timeout: 1500 }));
		});

		ws.addEventListener('message', (event) => {
			try {
				const msg: ScanProgress = JSON.parse(event.data);
				if (msg.type === 'found' && msg.ip) {
					found.push(msg.ip);
				}
				if (msg.type === 'error') {
					cleanup();
					reject(new Error(msg.error ?? 'Scan error'));
					return;
				}
				onProgress?.(msg);
				if (msg.type === 'done') {
					cleanup();
					resolve(found);
				}
			} catch { /* ignore parse errors */ }
		});

		ws.addEventListener('error', () => {
			reject(new Error('Failed to connect to scan proxy'));
		});

		ws.addEventListener('close', () => {
			// If not already resolved, resolve with what we have
			resolve(found);
		});
	});
}

/**
 * Save a TCP device entry to localStorage for auto-reconnect.
 */
export function saveTcpDevice(entry: TcpDeviceEntry) {
	const devices = loadTcpDevices();
	const key = `${entry.host}:${entry.port}`;
	const existing = devices.findIndex(d => `${d.host}:${d.port}` === key);
	if (existing >= 0) {
		devices[existing] = entry;
	} else {
		devices.push(entry);
	}
	localStorage.setItem(TCP_DEVICES_KEY, JSON.stringify(devices));
}

/**
 * Remove a TCP device from localStorage.
 */
export function removeTcpDevice(host: string, port: number) {
	const devices = loadTcpDevices();
	const key = `${host}:${port}`;
	const filtered = devices.filter(d => `${d.host}:${d.port}` !== key);
	localStorage.setItem(TCP_DEVICES_KEY, JSON.stringify(filtered));
}

/**
 * Load saved TCP devices from localStorage.
 */
export function loadTcpDevices(): TcpDeviceEntry[] {
	try {
		const raw = localStorage.getItem(TCP_DEVICES_KEY);
		return raw ? JSON.parse(raw) : [];
	} catch {
		return [];
	}
}
