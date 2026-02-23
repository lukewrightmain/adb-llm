/**
 * HTTP/1.1 Client over ADB Sockets.
 *
 * This is the CRITICAL service that eliminates the need for any backend.
 * It opens an ADB socket to a phone's TCP port (e.g. 8080) and performs
 * raw HTTP/1.1 request/response over the bidirectional ADB stream.
 *
 * Usage:
 *   const resp = await adbFetch(adb, '/api/ring');
 *   const data = JSON.parse(resp.text);
 *
 *   for await (const chunk of adbFetchSSE(adb, '/v1/chat/completions', {
 *     method: 'POST',
 *     body: JSON.stringify({ messages: [...], stream: true }),
 *   })) {
 *     console.log(chunk); // SSE data line content
 *   }
 */

import type { Adb } from '@yume-chan/adb';
import { buildHttpRequest, parseHttpResponse, type HttpResponse } from '$lib/utils/http-parser';

export interface AdbHttpOptions {
	method?: string;
	headers?: Record<string, string>;
	body?: string;
	/** Port on the phone to connect to (default: 8080) */
	port?: number;
	/** Abort signal for cancellation */
	signal?: AbortSignal;
}

export interface AdbHttpResponse {
	status: number;
	statusText: string;
	headers: Map<string, string>;
	body: Uint8Array;
	text: string;
	ok: boolean;
	json(): any;
}

/**
 * Perform an HTTP request over an ADB socket.
 *
 * Opens `adb.createSocket("tcp:PORT")`, writes a raw HTTP request,
 * reads and parses the raw HTTP response.
 */
export async function adbFetch(
	adb: Adb,
	path: string,
	options: AdbHttpOptions = {}
): Promise<AdbHttpResponse> {
	const { method = 'GET', headers = {}, body, port = 8080, signal } = options;

	// Check if already aborted
	if (signal?.aborted) {
		throw new DOMException('Request aborted', 'AbortError');
	}

	// Open ADB socket to phone's TCP port
	const socket = await adb.createSocket(`tcp:${port}`);

	try {
		// Build raw HTTP request
		const requestBytes = buildHttpRequest(method, path, headers, body);

		// Write request to the socket
		const writer = socket.writable.getWriter();
		try {
			await writer.write(requestBytes);
			// Signal we're done writing (half-close)
			await writer.close();
		} catch (e) {
			writer.releaseLock();
			throw e;
		}

		// Set up abort handler
		let abortHandler: (() => void) | undefined;
		if (signal) {
			abortHandler = () => {
				try {
					socket.close();
				} catch { /* ignore */ }
			};
			signal.addEventListener('abort', abortHandler, { once: true });
		}

		try {
			// Read and parse response
			const reader = socket.readable.getReader();
			const rawResponse = await parseHttpResponse(reader);
			reader.releaseLock();

			return wrapResponse(rawResponse);
		} finally {
			if (abortHandler) {
				signal!.removeEventListener('abort', abortHandler);
			}
		}
	} finally {
		try {
			await socket.close();
		} catch { /* already closed */ }
	}
}

/**
 * Perform a streaming SSE request over an ADB socket.
 *
 * Yields individual `data:` line contents from SSE events.
 * Stops when it receives `data: [DONE]` or the stream closes.
 */
export async function* adbFetchSSE(
	adb: Adb,
	path: string,
	options: AdbHttpOptions = {}
): AsyncGenerator<string, void, undefined> {
	const { method = 'POST', headers = {}, body, port = 8080, signal } = options;

	if (signal?.aborted) {
		throw new DOMException('Request aborted', 'AbortError');
	}

	const socket = await adb.createSocket(`tcp:${port}`);

	try {
		// Build request
		const requestBytes = buildHttpRequest(method, path, {
			...headers,
			'Accept': 'text/event-stream',
		}, body);

		// Write request
		const writer = socket.writable.getWriter();
		await writer.write(requestBytes);
		await writer.close();

		// Set up abort handler
		let aborted = false;
		let abortHandler: (() => void) | undefined;
		if (signal) {
			abortHandler = () => {
				aborted = true;
				try { socket.close(); } catch { /* ignore */ }
			};
			signal.addEventListener('abort', abortHandler, { once: true });
		}

		try {
			const reader = socket.readable.getReader();
			const decoder = new TextDecoder();
			let buffer = '';
			let headersParsed = false;

			while (!aborted) {
				const { done, value } = await reader.read();
				if (done) break;

				buffer += decoder.decode(value, { stream: true });

				// Skip headers on first chunk
				if (!headersParsed) {
					const headerEnd = buffer.indexOf('\r\n\r\n');
					if (headerEnd === -1) continue;
					buffer = buffer.slice(headerEnd + 4);
					headersParsed = true;
				}

				// Process SSE lines
				const lines = buffer.split('\n');
				buffer = lines.pop() || ''; // Keep incomplete line

				for (const line of lines) {
					const trimmed = line.trim();
					if (!trimmed.startsWith('data: ')) continue;
					const data = trimmed.slice(6);
					if (data === '[DONE]') return;
					yield data;
				}
			}

			// Process any remaining buffer
			if (buffer.trim().startsWith('data: ')) {
				const data = buffer.trim().slice(6);
				if (data !== '[DONE]') yield data;
			}

			reader.releaseLock();
		} finally {
			if (abortHandler) {
				signal!.removeEventListener('abort', abortHandler);
			}
		}
	} finally {
		try {
			await socket.close();
		} catch { /* already closed */ }
	}
}

/**
 * Wrap raw HttpResponse into a more ergonomic interface.
 */
function wrapResponse(raw: HttpResponse): AdbHttpResponse {
	const decoder = new TextDecoder();
	const text = decoder.decode(raw.body);
	return {
		status: raw.status,
		statusText: raw.statusText,
		headers: raw.headers,
		body: raw.body,
		text,
		ok: raw.status >= 200 && raw.status < 300,
		json() {
			return JSON.parse(text);
		},
	};
}
