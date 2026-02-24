#!/usr/bin/env node
/**
 * ADB Proxy — WebSocket bridge + HTTP shell API + Network Scanner
 *
 * HTTP routes (for ring orchestration — no WebSocket needed):
 *   POST /shell       — Run `adb -s {serial} shell {cmd}`, returns {ok, stdout, stderr}
 *   POST /shell/batch — Run commands on multiple devices sequentially
 *   GET  /ping        — Health check
 *
 * WebSocket routes (for browser ADB protocol pass-through):
 *   ws://host:3002/adb/{ip}:{port}  — Bridge WS ↔ TCP
 *   ws://host:3002/scan              — Network scanner
 *
 * Usage: node ws-proxy.mjs [--port 3002]
 */

import { WebSocketServer } from 'ws';
import { createServer } from 'http';
import { createConnection } from 'net';
import { execFile } from 'child_process';
import { URL } from 'url';
import { access, constants } from 'fs/promises';
import { resolve } from 'path';

const PORT = parseInt(process.argv.find((_, i, a) => a[i - 1] === '--port') ?? '3002', 10);
let ADB = process.env.ADB_PATH || `${process.env.HOME}/.local/bin/adb`;
const SCAN_CONCURRENCY = 64;    // Max parallel TCP probes
const SCAN_TIMEOUT_MS = 1500;   // Per-probe timeout
const SHELL_TIMEOUT_MS = 30000; // Per-command timeout

const PING_INTERVAL_MS = 30000; // Keepalive ping every 30s

/**
 * Test if an ADB binary path is valid and executable.
 * Returns { valid, version, error }.
 */
function testAdbPath(path) {
	return new Promise((resolve) => {
		execFile(path, ['version'], { timeout: 5000 }, (error, stdout) => {
			if (error) {
				resolve({ valid: false, error: error.message });
			} else {
				const version = stdout.split('\n')[0] || stdout.trim();
				resolve({ valid: true, version });
			}
		});
	});
}

/** Common ADB locations to check on Linux/macOS */
const COMMON_ADB_PATHS = [
	`${process.env.HOME}/.local/bin/adb`,
	'/usr/bin/adb',
	'/usr/local/bin/adb',
	`${process.env.HOME}/Android/Sdk/platform-tools/adb`,
	`${process.env.HOME}/platform-tools/adb`,
	'/opt/android-sdk/platform-tools/adb',
];

// ─── HTTP Server ──────────────────────────────────────────────────────

const httpServer = createServer(async (req, res) => {
	// CORS headers for dashboard
	res.setHeader('Access-Control-Allow-Origin', '*');
	res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
	res.setHeader('Access-Control-Allow-Headers', 'Content-Type');

	if (req.method === 'OPTIONS') {
		res.writeHead(204);
		res.end();
		return;
	}

	const url = new URL(req.url, `http://${req.headers.host}`);

	if (url.pathname === '/ping' && req.method === 'GET') {
		res.writeHead(200, { 'Content-Type': 'application/json' });
		res.end(JSON.stringify({ ok: true, adbPath: ADB }));
		return;
	}

	if (url.pathname === '/config' && req.method === 'GET') {
		res.writeHead(200, { 'Content-Type': 'application/json' });
		res.end(JSON.stringify({ ok: true, adbPath: ADB }));
		return;
	}

	if (url.pathname === '/config' && req.method === 'POST') {
		const body = await readBody(req);
		try {
			const { adbPath } = JSON.parse(body);
			if (adbPath && typeof adbPath === 'string') {
				// Validate the path before accepting it
				const test = await testAdbPath(adbPath);
				if (!test.valid) {
					res.writeHead(200, { 'Content-Type': 'application/json' });
					res.end(JSON.stringify({ ok: false, error: `ADB not found at "${adbPath}": ${test.error}`, adbPath: ADB }));
					return;
				}
				ADB = adbPath;
				console.log(`[config] ADB path updated to: ${ADB} (${test.version})`);
				res.writeHead(200, { 'Content-Type': 'application/json' });
				res.end(JSON.stringify({ ok: true, adbPath: ADB, version: test.version }));
			} else {
				res.writeHead(200, { 'Content-Type': 'application/json' });
				res.end(JSON.stringify({ ok: true, adbPath: ADB }));
			}
		} catch (e) {
			res.writeHead(400, { 'Content-Type': 'application/json' });
			res.end(JSON.stringify({ ok: false, error: e.message }));
		}
		return;
	}

	// Test a specific ADB path without changing the current one
	if (url.pathname === '/config/test' && req.method === 'POST') {
		const body = await readBody(req);
		try {
			const { adbPath } = JSON.parse(body);
			if (!adbPath) {
				res.writeHead(400, { 'Content-Type': 'application/json' });
				res.end(JSON.stringify({ ok: false, error: 'adbPath required' }));
				return;
			}
			const test = await testAdbPath(adbPath);
			res.writeHead(200, { 'Content-Type': 'application/json' });
			res.end(JSON.stringify({ ok: test.valid, version: test.version, error: test.error }));
		} catch (e) {
			res.writeHead(400, { 'Content-Type': 'application/json' });
			res.end(JSON.stringify({ ok: false, error: e.message }));
		}
		return;
	}

	// Auto-detect working ADB paths on the proxy server
	if (url.pathname === '/config/detect' && req.method === 'GET') {
		const found = [];
		// Also try `which adb`
		const whichResult = await new Promise((resolve) => {
			execFile('which', ['adb'], { timeout: 3000 }, (err, stdout) => {
				resolve(err ? null : stdout.trim());
			});
		});
		const candidates = [...COMMON_ADB_PATHS];
		if (whichResult && !candidates.includes(whichResult)) candidates.unshift(whichResult);

		for (const p of candidates) {
			const test = await testAdbPath(p);
			if (test.valid) found.push({ path: p, version: test.version });
		}
		res.writeHead(200, { 'Content-Type': 'application/json' });
		res.end(JSON.stringify({ ok: true, found, current: ADB }));
		return;
	}

	if (url.pathname === '/shell' && req.method === 'POST') {
		const body = await readBody(req);
		try {
			const { serial, cmd } = JSON.parse(body);
			if (!serial || !cmd) {
				res.writeHead(400, { 'Content-Type': 'application/json' });
				res.end(JSON.stringify({ ok: false, error: 'serial and cmd required' }));
				return;
			}
			const result = await adbShell(serial, cmd);
			res.writeHead(200, { 'Content-Type': 'application/json' });
			res.end(JSON.stringify(result));
		} catch (e) {
			res.writeHead(500, { 'Content-Type': 'application/json' });
			res.end(JSON.stringify({ ok: false, error: e.message }));
		}
		return;
	}

	if (url.pathname === '/shell/batch' && req.method === 'POST') {
		const body = await readBody(req);
		try {
			const { commands } = JSON.parse(body);
			// commands = [{serial, cmd}, ...]
			if (!Array.isArray(commands)) {
				res.writeHead(400, { 'Content-Type': 'application/json' });
				res.end(JSON.stringify({ ok: false, error: 'commands must be an array' }));
				return;
			}
			const results = [];
			for (const { serial, cmd } of commands) {
				results.push(await adbShell(serial, cmd));
			}
			res.writeHead(200, { 'Content-Type': 'application/json' });
			res.end(JSON.stringify({ ok: true, results }));
		} catch (e) {
			res.writeHead(500, { 'Content-Type': 'application/json' });
			res.end(JSON.stringify({ ok: false, error: e.message }));
		}
		return;
	}

	// Not found
	res.writeHead(404, { 'Content-Type': 'application/json' });
	res.end(JSON.stringify({ error: 'Not found' }));
});

function readBody(req) {
	return new Promise((resolve, reject) => {
		let data = '';
		req.on('data', chunk => data += chunk);
		req.on('end', () => resolve(data));
		req.on('error', reject);
	});
}

/**
 * Run `adb -s {serial} shell {cmd}` using the server's local ADB binary.
 * No WebSocket, no browser ADB — just a direct subprocess call.
 */
function adbShell(serial, cmd) {
	return new Promise((resolve) => {
		const args = ['-s', serial, 'shell', cmd];
		console.log(`[shell] ${serial}: ${cmd.slice(0, 100)}${cmd.length > 100 ? '...' : ''}`);

		execFile(ADB, args, { timeout: SHELL_TIMEOUT_MS, maxBuffer: 1024 * 1024 }, (error, stdout, stderr) => {
			if (error) {
				console.log(`[shell] ${serial}: ERROR ${error.message.slice(0, 80)}`);
				resolve({ ok: false, error: error.message, stdout: stdout || '', stderr: stderr || '' });
			} else {
				resolve({ ok: true, stdout: stdout || '', stderr: stderr || '' });
			}
		});
	});
}

// ─── WebSocket Server (attached to HTTP server) ───────────────────────

const wss = new WebSocketServer({ server: httpServer });

httpServer.listen(PORT, '0.0.0.0', () => {
	console.log(`[ws-proxy] Listening on http://0.0.0.0:${PORT}`);
	console.log(`[ws-proxy] ADB: ${ADB}`);
	console.log(`[ws-proxy] HTTP routes:`);
	console.log(`  POST /shell          — Run ADB shell command`);
	console.log(`  POST /shell/batch    — Run commands on multiple devices`);
	console.log(`  GET/POST /config     — Get/set ADB path`);
	console.log(`  GET  /ping           — Health check`);
	console.log(`[ws-proxy] WebSocket routes:`);
	console.log(`  ws://host:${PORT}/adb/{ip}:{port}  — ADB bridge`);
	console.log(`  ws://host:${PORT}/scan              — Network scanner`);
});

wss.on('connection', (ws, req) => {
	const url = new URL(req.url, `http://${req.headers.host}`);

	if (url.pathname === '/scan') {
		handleScan(ws);
	} else if (url.pathname.startsWith('/adb/')) {
		handleAdbBridge(ws, url.pathname);
	} else {
		ws.close(4000, 'Unknown route');
	}
});

// ─── ADB Bridge ───────────────────────────────────────────────────────

function handleAdbBridge(ws, pathname) {
	const match = pathname.match(/^\/adb\/([^:]+):(\d+)$/);
	if (!match) {
		ws.close(4000, 'Invalid route. Use /adb/{ip}:{port}');
		return;
	}

	const [, targetHost, targetPort] = match;
	const port = parseInt(targetPort, 10);

	if (port < 1 || port > 65535) {
		ws.close(4001, 'Invalid port');
		return;
	}

	console.log(`[adb] Connecting to ${targetHost}:${port}`);

	const tcp = createConnection({ host: targetHost, port, keepAlive: true, keepAliveInitialDelay: 10000 }, () => {
		console.log(`[adb] TCP connected to ${targetHost}:${port}`);
	});

	tcp.on('data', (data) => {
		if (ws.readyState === ws.OPEN) ws.send(data);
	});

	tcp.on('error', (err) => {
		console.log(`[adb] TCP error ${targetHost}:${port}: ${err.message}`);
		ws.close(4002, `TCP error: ${err.message}`);
	});

	tcp.on('close', () => {
		console.log(`[adb] TCP closed ${targetHost}:${port}`);
		ws.close(1000, 'TCP closed');
	});

	ws.on('message', (data) => {
		if (tcp.writable) tcp.write(data);
	});

	// Keepalive: ping every 30s, require 2 consecutive missed pongs before terminating.
	// Under heavy load (ring formation), the browser event loop may delay pong responses.
	let missedPongs = 0;
	const pingTimer = setInterval(() => {
		if (missedPongs >= 2) {
			console.log(`[adb] 2 missed pongs, closing ${targetHost}:${port}`);
			clearInterval(pingTimer);
			ws.terminate();
			return;
		}
		missedPongs++;
		if (ws.readyState === ws.OPEN) ws.ping();
	}, PING_INTERVAL_MS);

	ws.on('pong', () => { missedPongs = 0; });

	ws.on('close', () => { clearInterval(pingTimer); tcp.destroy(); });
	ws.on('error', () => { clearInterval(pingTimer); tcp.destroy(); });
}

// ─── Network Scanner ──────────────────────────────────────────────────

function handleScan(ws) {
	ws.on('message', async (raw) => {
		let request;
		try {
			request = JSON.parse(raw.toString());
		} catch {
			ws.send(JSON.stringify({ error: 'Invalid JSON' }));
			return;
		}

		const { ips, port = 5555, timeout = SCAN_TIMEOUT_MS } = request;

		if (!Array.isArray(ips) || ips.length === 0) {
			ws.send(JSON.stringify({ error: 'ips must be a non-empty array of IP strings' }));
			return;
		}

		// Cap scan size
		if (ips.length > 65536) {
			ws.send(JSON.stringify({ error: `Too many IPs: ${ips.length} (max 65536)` }));
			return;
		}

		const total = ips.length;
		let scanned = 0;
		let found = 0;
		const startTime = Date.now();

		console.log(`[scan] Starting scan of ${total} IPs on port ${port} (timeout ${timeout}ms, concurrency ${SCAN_CONCURRENCY})`);

		ws.send(JSON.stringify({ type: 'start', total }));

		// Process in batches for controlled concurrency
		const batches = [];
		for (let i = 0; i < ips.length; i += SCAN_CONCURRENCY) {
			batches.push(ips.slice(i, i + SCAN_CONCURRENCY));
		}

		for (const batch of batches) {
			if (ws.readyState !== ws.OPEN) break;

			const results = await Promise.all(
				batch.map(ip => probePort(ip, port, timeout))
			);

			for (let j = 0; j < results.length; j++) {
				scanned++;
				if (results[j]) {
					found++;
					if (ws.readyState === ws.OPEN) {
						ws.send(JSON.stringify({ type: 'found', ip: batch[j], port }));
					}
				}
			}

			// Send progress update per batch
			if (ws.readyState === ws.OPEN) {
				ws.send(JSON.stringify({ type: 'progress', scanned, total, found }));
			}
		}

		const elapsed = Date.now() - startTime;
		console.log(`[scan] Done: ${found}/${total} devices found in ${elapsed}ms`);

		if (ws.readyState === ws.OPEN) {
			ws.send(JSON.stringify({ type: 'done', scanned, total, found, elapsedMs: elapsed }));
		}
	});
}

/**
 * Try to connect to ip:port with a timeout.
 * Returns true if connection succeeds (port open), false otherwise.
 */
function probePort(ip, port, timeout) {
	return new Promise((resolve) => {
		const socket = createConnection({ host: ip, port });

		const timer = setTimeout(() => {
			socket.destroy();
			resolve(false);
		}, timeout);

		socket.on('connect', () => {
			clearTimeout(timer);
			socket.destroy();
			resolve(true);
		});

		socket.on('error', () => {
			clearTimeout(timer);
			socket.destroy();
			resolve(false);
		});
	});
}

wss.on('error', (err) => {
	console.error(`[ws-proxy] Server error: ${err.message}`);
});
