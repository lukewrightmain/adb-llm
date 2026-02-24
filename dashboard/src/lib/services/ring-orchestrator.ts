/**
 * Ring Orchestrator — full ring lifecycle management via server-side ADB.
 *
 * Instead of managing WebSocket→ADB connections from the browser (fragile, connection-limited),
 * this sends HTTP requests to the proxy server which runs `adb shell` commands directly.
 * The server has native ADB access to all phones — no WebSocket dance needed.
 *
 * HTTP API (ws-proxy.mjs):
 *   POST /shell       — { serial, cmd } → { ok, stdout, stderr }
 *   POST /shell/batch — { commands: [{serial, cmd}, ...] } → { ok, results: [...] }
 */

import type { Adb } from '@yume-chan/adb';
import type { ManagedDevice, RingFormationConfig, RingFormationProgress, RingFormationPhase } from '$lib/types/adb';
import { calcLayerWeights } from './layer-calculator';

type ProgressCallback = (progress: RingFormationProgress) => void;
type ReconnectCallback = (serial: string, adb: Adb) => void;

/** Proxy server base URL — same host as the page, port 3002 */
function getProxyUrl(): string {
	return `http://${location.hostname}:3002`;
}

function emit(cb: ProgressCallback | undefined, phase: RingFormationPhase, message: string, step: number, totalSteps: number, progress = 0) {
	cb?.({ phase, message, progress, step, totalSteps });
}

/**
 * Push ADB path config to the proxy server.
 * The proxy uses this path for all subsequent `adb shell` commands.
 */
export async function configureProxy(adbPath?: string): Promise<void> {
	if (!adbPath) return;
	await fetch(`${getProxyUrl()}/config`, {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify({ adbPath }),
	});
}

/**
 * Get the current ADB path from the proxy.
 */
export async function getProxyConfig(): Promise<{ adbPath: string }> {
	const resp = await fetch(`${getProxyUrl()}/config`);
	return resp.json();
}

/**
 * Test a specific ADB path on the proxy server (doesn't change the active path).
 */
export async function testAdbPath(adbPath: string): Promise<{ ok: boolean; version?: string; error?: string }> {
	const resp = await fetch(`${getProxyUrl()}/config/test`, {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify({ adbPath }),
	});
	return resp.json();
}

/**
 * Auto-detect available ADB paths on the proxy server.
 */
export async function detectAdbPaths(): Promise<{ found: { path: string; version: string }[]; current: string }> {
	const resp = await fetch(`${getProxyUrl()}/config/detect`);
	return resp.json();
}

// ─── Server-Side ADB Shell ───────────────────────────────────────────

/**
 * Run an ADB shell command via the proxy server's HTTP API.
 * The server runs `adb -s {serial} shell {cmd}` as a subprocess.
 * No WebSocket, no browser ADB auth — just a simple HTTP call.
 */
async function serverShell(serial: string, cmd: string): Promise<string> {
	const resp = await fetch(`${getProxyUrl()}/shell`, {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify({ serial, cmd }),
	});

	if (!resp.ok) {
		throw new Error(`Proxy HTTP error ${resp.status}: ${await resp.text()}`);
	}

	const result = await resp.json();
	if (!result.ok) {
		throw new Error(`ADB shell error on ${serial}: ${result.error}`);
	}

	return result.stdout;
}

/**
 * Run an ADB shell command with retries.
 */
async function resilientShell(serial: string, cmd: string, maxRetries = 2): Promise<string> {
	for (let attempt = 0; attempt <= maxRetries; attempt++) {
		try {
			return await serverShell(serial, cmd);
		} catch (e) {
			console.warn(`[ring] Shell failed on ${serial} (attempt ${attempt + 1}/${maxRetries + 1}): ${e}`);
			if (attempt < maxRetries) {
				await sleep(1000 + attempt * 1000);
				continue;
			}
			throw e;
		}
	}
	throw new Error(`Shell failed after ${maxRetries + 1} attempts on ${serial}`);
}

/**
 * Check if a process is running on a device.
 */
async function serverPidof(serial: string, name: string): Promise<number | null> {
	const stdout = await serverShell(serial, `pidof ${name} 2>/dev/null || true`);
	const pid = parseInt(stdout.trim(), 10);
	return isNaN(pid) ? null : pid;
}

/**
 * Fetch an HTTP endpoint on a device (via adb shell + curl).
 * For checking cellswarm-master's /api/ring endpoint.
 */
async function serverHttpCheck(serial: string, ip: string, port: number, path: string): Promise<any> {
	// Use curl — wget is not available on these Android 15 phones
	const stdout = await serverShell(serial, `curl -s http://${ip}:${port}${path} 2>/dev/null || true`);
	if (!stdout.trim()) return null;
	try {
		return JSON.parse(stdout.trim());
	} catch {
		return null;
	}
}

// ─── Model Path Resolution ───────────────────────────────────────────

/** Directories to search for model files (same as model-manager.ts) */
const MODEL_SEARCH_DIRS = [
	'/data/local/tmp/cellswarm/models',
	'/data/local/tmp/adb-llm/models',
	'/data/local/tmp/models',
	'/sdcard/Download',
	'/sdcard/Models',
	'/data/local/tmp',
];

/**
 * Find the actual full path of a model file on a device.
 * Searches multiple directories and follows symlinks.
 * Returns the resolved (real) path so the binary can open the file.
 */
async function resolveModelOnDevice(serial: string, modelName: string): Promise<string> {
	const dirs = MODEL_SEARCH_DIRS.join(' ');
	// Use find + xargs instead of find -exec (backslash-semicolon gets eaten by shell layers)
	const stdout = await resilientShell(serial,
		`find ${dirs} -maxdepth 2 -name "${modelName}" 2>/dev/null | head -1 | xargs readlink -f 2>/dev/null`
	);
	const resolved = stdout.trim();
	if (!resolved) {
		throw new Error(`Model "${modelName}" not found on device ${serial}. Searched: ${dirs}`);
	}
	return resolved;
}

/**
 * Kill all cellswarm processes on the given devices.
 * Uses kill-by-PID as fallback since pkill can be unreliable on some Android builds.
 * Also sets fast port reuse sysctl and waits for ZMQ ports to release.
 */
export async function killAllProcesses(devices: ManagedDevice[], dataPort = 9100, signalPort = 10100): Promise<void> {
	for (const d of devices) {
		try {
			await resilientShell(d.serial,
				'pkill -9 cellswarm-worker 2>/dev/null; pkill -9 cellswarm-worker-spec 2>/dev/null; pkill -9 cellswarm-master 2>/dev/null; pkill -9 mdns-advertise 2>/dev/null; ' +
				'for p in $(ps -A -o PID,NAME 2>/dev/null | grep cellswarm | awk \'{print $1}\'); do kill -9 $p 2>/dev/null; done; ' +
				// Enable fast port reuse + reduce FIN timeout so ZMQ ports release quickly
				'echo 1 > /proc/sys/net/ipv4/tcp_tw_reuse 2>/dev/null; ' +
				'echo 5 > /proc/sys/net/ipv4/tcp_fin_timeout 2>/dev/null; ' +
				'echo OK'
			);
		} catch { /* ignore */ }
	}

	// Wait for ZMQ sockets to release, then verify ports are free
	const maxWait = 15_000;
	const tick = 2_000;
	const start = Date.now();
	while (Date.now() - start < maxWait) {
		await sleep(tick);
		// Check if data port is free on all devices
		let allFree = true;
		for (const d of devices) {
			try {
				const out = await serverShell(d.serial,
					`ss -tlnp 2>/dev/null | grep -E ":${dataPort} |:${signalPort} " | wc -l`
				);
				if (parseInt(out.trim()) > 0) {
					allFree = false;
					break;
				}
			} catch { /* ignore, treat as free */ }
		}
		if (allFree) {
			console.log(`[ring] Ports free after ${Math.round((Date.now() - start) / 1000)}s`);
			return;
		}
	}
	console.warn(`[ring] Ports may still be in use after ${maxWait / 1000}s wait`);
}

/**
 * Check if a port is available on all devices.
 * Returns true if the port is free on every device.
 */
async function isPortFreeOnAll(devices: ManagedDevice[], port: number): Promise<boolean> {
	for (const d of devices) {
		try {
			const out = await serverShell(d.serial,
				`ss -tlnp 2>/dev/null | grep ":${port} " | wc -l`
			);
			if (parseInt(out.trim()) > 0) return false;
		} catch { /* treat errors as free */ }
	}
	return true;
}

/**
 * Find available data+signal port pair across all devices.
 * Starts from the requested ports and increments by 100 if occupied.
 */
export async function findFreePorts(
	devices: ManagedDevice[],
	preferData = 9100,
	preferSignal = 10100
): Promise<{ dataPort: number; signalPort: number }> {
	for (let offset = 0; offset < 500; offset += 100) {
		const dp = preferData + offset;
		const sp = preferSignal + offset;
		const [dataFree, sigFree] = await Promise.all([
			isPortFreeOnAll(devices, dp),
			isPortFreeOnAll(devices, sp),
		]);
		if (dataFree && sigFree) {
			if (offset > 0) console.log(`[ring] Ports ${preferData}/${preferSignal} occupied, using ${dp}/${sp}`);
			return { dataPort: dp, signalPort: sp };
		}
	}
	// Fall back to requested ports and hope for the best
	console.warn(`[ring] Could not find free port pair, using requested ${preferData}/${preferSignal}`);
	return { dataPort: preferData, signalPort: preferSignal };
}

/**
 * Kill old cellswarm sessions on given serials. Returns per-device results.
 * Used by the Settings "cleanup" button — works without ManagedDevice objects.
 */
export async function cleanupOldSessions(serials: string[]): Promise<{ serial: string; killed: number }[]> {
	const results: { serial: string; killed: number }[] = [];

	for (const serial of serials) {
		try {
			// Count running cellswarm processes before kill
			const before = await serverShell(serial, 'ps -A -o PID,NAME 2>/dev/null | grep cellswarm | wc -l || echo 0');
			const count = parseInt(before.trim(), 10) || 0;

			if (count > 0) {
				// Kill by name and then by PID as fallback
				await serverShell(serial,
					'pkill -9 cellswarm-worker 2>/dev/null; pkill -9 cellswarm-worker-spec 2>/dev/null; pkill -9 cellswarm-master 2>/dev/null; pkill -9 mdns-advertise 2>/dev/null; ' +
					'for p in $(ps -A -o PID,NAME 2>/dev/null | grep cellswarm | awk \'{print $1}\'); do kill -9 $p 2>/dev/null; done; echo OK'
				);
			}

			results.push({ serial, killed: count });
		} catch {
			results.push({ serial, killed: 0 });
		}
	}

	return results;
}

/**
 * Form a ring and start inference.
 */
export async function startRing(
	config: RingFormationConfig,
	onProgress?: ProgressCallback,
	_onReconnect?: ReconnectCallback
): Promise<void> {
	const { devices, totalLayers, contextSize, threads, taskset, prefetch, httpPort } = config;
	const nPhones = devices.length;
	const totalSteps = nPhones + 3; // kill + N workers + master + verify

	if (nPhones < 2) {
		throw new Error('Ring requires at least 2 devices');
	}

	// Verify proxy is reachable
	try {
		const resp = await fetch(`${getProxyUrl()}/ping`);
		if (!resp.ok) throw new Error('not ok');
	} catch {
		throw new Error('Proxy server not reachable. Is ws-proxy.mjs running?');
	}

	// Push ADB path config if set
	if (config.adbPath) {
		await configureProxy(config.adbPath);
	}

	// Step 1: Kill existing processes
	emit(onProgress, 'killing', 'Stopping existing processes...', 1, totalSteps);
	await killAllProcesses(devices, config.dataPort, config.signalPort);

	// Find free ports (auto-increment if requested ports are occupied by orphan sockets)
	emit(onProgress, 'killing', 'Finding free ports...', 1, totalSteps, 0.2);
	const { dataPort, signalPort } = await findFreePorts(devices, config.dataPort, config.signalPort);
	if (dataPort !== config.dataPort || signalPort !== config.signalPort) {
		emit(onProgress, 'killing', `Using ports ${dataPort}/${signalPort} (default occupied)`, 1, totalSteps, 0.3);
	}

	// Verify binaries exist on all devices
	emit(onProgress, 'killing', 'Verifying binaries on devices...', 1, totalSteps, 0.4);
	const missingBinaries: string[] = [];
	for (const d of devices) {
		const workerBin = d === devices[0] ? 'cellswarm-master' : 'cellswarm-worker';
		const stdout = await serverShell(d.serial,
			`test -x /data/local/tmp/cellswarm/bin/${workerBin} && echo YES || echo NO`
		);
		if (!stdout.trim().includes('YES')) {
			missingBinaries.push(`${d.serial}: ${workerBin}`);
		}
	}
	if (missingBinaries.length > 0) {
		throw new Error(`Missing binaries on devices:\n${missingBinaries.join('\n')}\n\nDeploy binaries first (Settings or manual push).`);
	}

	// Calculate layer distribution
	const lw = calcLayerWeights(totalLayers, nPhones);
	emit(onProgress, 'killing', `Layer weights: ${lw}`, 1, totalSteps, 0.6);

	// Resolve IP addresses for all devices
	const ips = new Map<string, string>();
	for (const d of devices) {
		const serial = d.serial;
		if (serial.match(/^\d+\.\d+\.\d+\.\d+:\d+$/)) {
			ips.set(serial, serial.split(':')[0]);
		} else {
			const stdout = await resilientShell(serial, "ip route get 1.1.1.1 2>/dev/null | grep -oP 'src \\K[0-9.]+' || echo ''");
			const ip = stdout.trim();
			if (!ip) throw new Error(`Could not resolve IP for device ${serial}`);
			ips.set(serial, ip);
		}
	}

	const masterIp = ips.get(devices[0].serial)!;

	// Resolve model paths per device (models may be in different directories on different phones)
	emit(onProgress, 'killing', 'Resolving model paths on devices...', 1, totalSteps, 0.8);
	const modelPaths = new Map<string, string>();
	const draftPaths = new Map<string, string>();
	for (const d of devices) {
		const resolved = await resolveModelOnDevice(d.serial, config.modelPath);
		modelPaths.set(d.serial, resolved);
		console.log(`[ring] ${d.serial}: model → ${resolved}`);

		if (config.draftModelPath) {
			const draftResolved = await resolveModelOnDevice(d.serial, config.draftModelPath);
			draftPaths.set(d.serial, draftResolved);
			console.log(`[ring] ${d.serial}: draft → ${draftResolved}`);
		}
	}

	// Step 2: Start workers (rank 1 to N-1) — one at a time via server ADB
	emit(onProgress, 'starting-workers', `Starting ${nPhones - 1} workers...`, 2, totalSteps);

	let startedCount = 0;
	const failedRanks: number[] = [];
	for (let i = 0; i < devices.length - 1; i++) {
		const device = devices[i + 1];
		const idx = i + 1;
		const nextIdx = (idx + 1) % nPhones;
		const nextIp = ips.get(devices[nextIdx].serial)!;

		const cmd = buildWorkerCommand({
			modelFullPath: modelPaths.get(device.serial)!,
			world: nPhones,
			rank: idx,
			masterIp,
			nextIp,
			dataPort,
			signalPort,
			lw,
			contextSize,
			threads,
			taskset,
			prefetch,
		});

		try {
			await resilientShell(device.serial, cmd);
			// Brief pause then verify process is running
			await sleep(500);
			const pid = await serverPidof(device.serial, 'cellswarm-worker');
			if (pid === null) {
				const log = await serverShell(device.serial, 'tail -5 /data/local/tmp/cellswarm-worker.log 2>/dev/null || echo "No log"').catch(() => 'Could not read log');
				console.warn(`[ring] Worker rank ${idx} launched but not running. Log: ${log}`);
				failedRanks.push(idx);
			} else {
				startedCount++;
				emit(onProgress, 'starting-workers', `Started worker ${startedCount}/${nPhones - 1} (rank ${idx}, pid ${pid})`, 2, totalSteps, startedCount / (nPhones - 1));
			}
		} catch (e) {
			console.warn(`[ring] Worker rank ${idx} failed:`, e);
			failedRanks.push(idx);
		}
	}

	if (failedRanks.length > 0) {
		emit(onProgress, 'starting-workers', `Started ${startedCount}/${nPhones - 1} workers (${failedRanks.length} failed)`, 2, totalSteps, 1);
	} else {
		emit(onProgress, 'starting-workers', `All ${nPhones - 1} workers started`, 2, totalSteps, 1);
	}

	// Step 3: Wait for workers to load model (just wait — no polling needed)
	emit(onProgress, 'loading-model', 'Waiting for workers to load model (~2 min)...', nPhones, totalSteps);

	const LOAD_WAIT_MS = 120_000;
	const TICK_MS = 5_000;
	const loadStart = Date.now();

	while (Date.now() - loadStart < LOAD_WAIT_MS) {
		await sleep(TICK_MS);
		const elapsed = Math.round((Date.now() - loadStart) / 1000);
		emit(onProgress, 'loading-model', `Workers loading model... (${elapsed}s)`, nPhones, totalSteps, Math.min((Date.now() - loadStart) / LOAD_WAIT_MS, 0.95));
	}

	// Step 4: Start master (rank 0)
	emit(onProgress, 'starting-master', 'Starting master (rank 0)...', nPhones + 1, totalSteps);

	const nextIp = ips.get(devices[1].serial)!;
	const masterSerial = devices[0].serial;
	const masterCmd = buildMasterCommand({
		modelFullPath: modelPaths.get(masterSerial)!,
		draftModelFullPath: config.draftModelPath ? draftPaths.get(masterSerial) : undefined,
		draftMax: config.draftMax,
		world: nPhones,
		masterIp,
		nextIp,
		dataPort,
		signalPort,
		lw,
		contextSize,
		threads,
		taskset,
		prefetch,
		httpPort,
	});

	await resilientShell(devices[0].serial, masterCmd, 3);
	await sleep(10_000);

	// Verify master is running
	const masterPid = await serverPidof(devices[0].serial, 'cellswarm-master');
	if (masterPid === null) {
		let log = 'Could not read log';
		try {
			log = await serverShell(devices[0].serial, 'tail -20 /data/local/tmp/cellswarm-master.log 2>/dev/null || echo "No log"');
		} catch { /* ignore */ }
		throw new Error(`Master failed to start. Log:\n${log}`);
	}

	// Step 5: Verify ring via HTTP (check master's /api/ring endpoint)
	emit(onProgress, 'verifying', 'Verifying ring health...', nPhones + 2, totalSteps);

	const verifyTimeout = 60_000;
	const verifyStart = Date.now();
	while (Date.now() - verifyStart < verifyTimeout) {
		try {
			const data = await serverHttpCheck(devices[0].serial, masterIp, httpPort, '/api/ring');
			if (data && (data.n_world > 0 || data.world_size > 0)) {
				emit(onProgress, 'ready', `Ring ready! ${nPhones} nodes.`, totalSteps, totalSteps, 1);
				return;
			}
		} catch { /* ignore */ }
		await sleep(3000);
	}

	// Master is running but ring didn't report ready in time
	emit(onProgress, 'ready', `Master running (pid ${masterPid}). Ring may still be loading.`, totalSteps, totalSteps, 1);
}

/**
 * Stop all ring processes on devices.
 */
export async function stopRing(devices: ManagedDevice[]): Promise<void> {
	await killAllProcesses(devices);
}

// ─── Command Builders ────────────────────────────────────────────────

interface WorkerCmdParams {
	/** Fully resolved model path on this specific device */
	modelFullPath: string;
	world: number;
	rank: number;
	masterIp: string;
	nextIp: string;
	dataPort: number;
	signalPort: number;
	lw: string;
	contextSize: number;
	threads: number;
	taskset: string;
	prefetch: boolean;
}

function buildWorkerCommand(p: WorkerCmdParams): string {
	const prefetchFlag = p.prefetch ? '--prefetch' : '';

	// Subshell + setsid daemonize: (setsid CMD &) </dev/null >/dev/null 2>&1
	// This fully detaches from adb shell so it returns immediately (46ms vs 30s timeout).
	// Log redirect is inside the setsid subshell so output is captured.
	const innerCmd =
		`taskset ${p.taskset} /data/local/tmp/cellswarm/bin/cellswarm-worker ` +
		`-m ${p.modelFullPath} ` +
		`--world ${p.world} --rank ${p.rank} ` +
		`--master ${p.masterIp} --next ${p.nextIp} ` +
		`--data-port ${p.dataPort} --signal-port ${p.signalPort} ` +
		`-lw ${p.lw} -c ${p.contextSize} -n -1 -t ${p.threads} -tb ${p.threads} ` +
		`--no-mmap ${prefetchFlag} ` +
		`> /data/local/tmp/cellswarm-worker.log 2>&1`;

	return `(setsid ${innerCmd} &) </dev/null >/dev/null 2>&1 && echo LAUNCHED`;
}

interface MasterCmdParams extends WorkerCmdParams {
	/** Fully resolved draft model path on master device */
	draftModelFullPath?: string;
	draftMax: number;
	httpPort: number;
}

function buildMasterCommand(p: MasterCmdParams): string {
	const prefetchFlag = p.prefetch ? '--prefetch' : '';

	let draftFlags = '';
	if (p.draftModelFullPath) {
		draftFlags = `--model-draft ${p.draftModelFullPath} --draft-max ${p.draftMax} `;
	}

	// Subshell + setsid daemonize pattern (same as worker)
	const innerCmd =
		`env SWARM_BATCH_PIPELINE=1 taskset ${p.taskset} /data/local/tmp/cellswarm/bin/cellswarm-master ` +
		`-m ${p.modelFullPath} ` +
		`${draftFlags}` +
		`--world ${p.world} --rank 0 ` +
		`--master ${p.masterIp} --next ${p.nextIp} ` +
		`--data-port ${p.dataPort} --signal-port ${p.signalPort} ` +
		`-lw ${p.lw} -c ${p.contextSize} -t ${p.threads} -tb ${p.threads} ` +
		`--no-mmap ${prefetchFlag} ` +
		`--host 0.0.0.0 --port ${p.httpPort} -np 1 ` +
		`> /data/local/tmp/cellswarm-master.log 2>&1`;

	return `(setsid ${innerCmd} &) </dev/null >/dev/null 2>&1 && echo LAUNCHED`;
}

function sleep(ms: number): Promise<void> {
	return new Promise(resolve => setTimeout(resolve, ms));
}
