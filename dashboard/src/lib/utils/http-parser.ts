/**
 * Raw HTTP/1.1 Response Parser.
 *
 * Parses status line, headers, and body from raw bytes.
 * Supports both Content-Length and Transfer-Encoding: chunked.
 * This is used to parse responses from ADB socket connections
 * where we don't have a browser fetch() API available.
 */

export interface HttpResponse {
	status: number;
	statusText: string;
	headers: Map<string, string>;
	body: Uint8Array;
}

const CRLF = new Uint8Array([0x0d, 0x0a]); // \r\n
const DOUBLE_CRLF = new Uint8Array([0x0d, 0x0a, 0x0d, 0x0a]); // \r\n\r\n

/**
 * Find the index of a byte pattern in a buffer.
 */
function findPattern(buf: Uint8Array, pattern: Uint8Array, start = 0): number {
	outer:
	for (let i = start; i <= buf.length - pattern.length; i++) {
		for (let j = 0; j < pattern.length; j++) {
			if (buf[i + j] !== pattern[j]) continue outer;
		}
		return i;
	}
	return -1;
}

/**
 * Concatenate multiple Uint8Arrays.
 */
function concat(...arrays: Uint8Array[]): Uint8Array {
	const totalLen = arrays.reduce((acc, a) => acc + a.length, 0);
	const result = new Uint8Array(totalLen);
	let offset = 0;
	for (const arr of arrays) {
		result.set(arr, offset);
		offset += arr.length;
	}
	return result;
}

/**
 * Parse an HTTP/1.1 response from a ReadableStream<Uint8Array>.
 *
 * Reads the status line and headers, then reads the body based on
 * Content-Length or chunked transfer encoding.
 */
export async function parseHttpResponse(
	reader: ReadableStreamDefaultReader<Uint8Array>
): Promise<HttpResponse> {
	const decoder = new TextDecoder();
	let buffer = new Uint8Array(0);

	// Read until we find \r\n\r\n (end of headers)
	while (findPattern(buffer, DOUBLE_CRLF) === -1) {
		const { done, value } = await reader.read();
		if (done) break;
		buffer = concat(buffer, value);
	}

	const headerEnd = findPattern(buffer, DOUBLE_CRLF);
	if (headerEnd === -1) {
		throw new Error('Incomplete HTTP response: no header terminator found');
	}

	// Parse headers section
	const headerBytes = buffer.slice(0, headerEnd);
	const headerText = decoder.decode(headerBytes);
	const headerLines = headerText.split('\r\n');

	// Status line: "HTTP/1.1 200 OK"
	const statusLine = headerLines[0];
	const statusMatch = statusLine.match(/^HTTP\/[\d.]+\s+(\d+)\s*(.*)/);
	if (!statusMatch) {
		throw new Error(`Invalid HTTP status line: ${statusLine}`);
	}
	const status = parseInt(statusMatch[1]);
	const statusText = statusMatch[2] || '';

	// Parse headers
	const headers = new Map<string, string>();
	for (let i = 1; i < headerLines.length; i++) {
		const colonIdx = headerLines[i].indexOf(':');
		if (colonIdx === -1) continue;
		const key = headerLines[i].slice(0, colonIdx).trim().toLowerCase();
		const val = headerLines[i].slice(colonIdx + 1).trim();
		headers.set(key, val);
	}

	// Body starts after \r\n\r\n
	let bodyBuffer = buffer.slice(headerEnd + 4);

	const transferEncoding = headers.get('transfer-encoding');
	const contentLength = headers.get('content-length');

	let body: Uint8Array;

	if (transferEncoding?.toLowerCase() === 'chunked') {
		body = await readChunkedBody(reader, bodyBuffer);
	} else if (contentLength !== undefined) {
		const len = parseInt(contentLength);
		body = await readFixedBody(reader, bodyBuffer, len);
	} else {
		// No Content-Length and not chunked — read until stream closes
		body = await readUntilClose(reader, bodyBuffer);
	}

	return { status, statusText, headers, body };
}

/**
 * Read a fixed-length body.
 */
async function readFixedBody(
	reader: ReadableStreamDefaultReader<Uint8Array>,
	initial: Uint8Array,
	length: number
): Promise<Uint8Array> {
	let buffer = initial;
	while (buffer.length < length) {
		const { done, value } = await reader.read();
		if (done) break;
		buffer = concat(buffer, value);
	}
	return buffer.slice(0, length);
}

/**
 * Read a chunked transfer-encoded body.
 */
async function readChunkedBody(
	reader: ReadableStreamDefaultReader<Uint8Array>,
	initial: Uint8Array
): Promise<Uint8Array> {
	let buffer = initial;
	const chunks: Uint8Array[] = [];

	while (true) {
		// Read until we find \r\n (chunk size line)
		while (findPattern(buffer, CRLF) === -1) {
			const { done, value } = await reader.read();
			if (done) {
				// No more data — return what we have
				return concatAll(chunks);
			}
			buffer = concat(buffer, value);
		}

		const lineEnd = findPattern(buffer, CRLF);
		const sizeHex = new TextDecoder().decode(buffer.slice(0, lineEnd)).trim();
		const chunkSize = parseInt(sizeHex, 16);

		if (isNaN(chunkSize) || chunkSize === 0) {
			// End of chunks
			break;
		}

		// Skip past the size line + \r\n
		buffer = buffer.slice(lineEnd + 2);

		// Read the chunk data + trailing \r\n
		const needed = chunkSize + 2; // chunk data + \r\n
		while (buffer.length < needed) {
			const { done, value } = await reader.read();
			if (done) break;
			buffer = concat(buffer, value);
		}

		chunks.push(buffer.slice(0, chunkSize));
		buffer = buffer.slice(needed);
	}

	return concatAll(chunks);
}

/**
 * Read until the stream closes.
 */
async function readUntilClose(
	reader: ReadableStreamDefaultReader<Uint8Array>,
	initial: Uint8Array
): Promise<Uint8Array> {
	const chunks: Uint8Array[] = [initial];
	while (true) {
		const { done, value } = await reader.read();
		if (done) break;
		chunks.push(value);
	}
	return concatAll(chunks);
}

function concatAll(arrays: Uint8Array[]): Uint8Array {
	if (arrays.length === 0) return new Uint8Array(0);
	if (arrays.length === 1) return arrays[0];
	return concat(...arrays);
}

/**
 * Build a raw HTTP/1.1 request as bytes.
 */
export function buildHttpRequest(
	method: string,
	path: string,
	headers: Record<string, string> = {},
	body?: string
): Uint8Array {
	const encoder = new TextEncoder();
	const lines: string[] = [];

	lines.push(`${method} ${path} HTTP/1.1`);
	lines.push('Host: localhost');
	lines.push('Connection: close');

	if (body) {
		const bodyBytes = encoder.encode(body);
		headers['Content-Length'] = String(bodyBytes.length);
		if (!headers['Content-Type']) {
			headers['Content-Type'] = 'application/json';
		}
	}

	for (const [key, val] of Object.entries(headers)) {
		lines.push(`${key}: ${val}`);
	}

	lines.push('');
	lines.push('');

	const headerBytes = encoder.encode(lines.join('\r\n'));

	if (body) {
		return concat(headerBytes, encoder.encode(body));
	}

	return headerBytes;
}
