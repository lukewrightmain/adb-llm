/**
 * Ring Orchestrator — full ring lifecycle management.
 *
 * Ported from launch_cellswarm_master.sh. Manages:
 * - Killing existing processes
 * - Starting workers (rank 1..N-1)
 * - Starting master (rank 0) with HTTP server
 * - Verifying ring health
 */

import type { Adb } from '@yume-chan/adb';
import type { ManagedDevice, RingFormationConfig, RingFormationProgress, RingFormationPhase } from '$lib/types/adb';
import { calcLayerWeights } from './layer-calculator';
import { shellCmd, pkill, pidof, getIpAddress } from './adb-shell';
import { adbFetch } from './adb-http';
import { REMOTE_BIN_DIR, REMOTE_MODEL_DIR } from './binary-registry';

type ProgressCallback = (progress: RingFormationProgress) => void;

function emit(cb: ProgressCallback | undefined, phase: RingFormationPhase, message: string, step: number, totalSteps: number, progress = 0) {
	cb?.({ phase, message, progress, step, totalSteps });
}

/**
 * Kill all cellswarm processes on the given devices.
 */
export async function killAllProcesses(devices: ManagedDevice[]): Promise<void> {
	await Promise.all(
		devices
			.filter(d => d.adb)
			.map(async (d) => {
				try {
					await pkill(d.adb!, 'cellswarm-worker');
					await pkill(d.adb!, 'cellswarm-worker-spec');
					await pkill(d.adb!, 'cellswarm-master');
					await pkill(d.adb!, 'mdns-advertise');
				} catch { /* ignore */ }
			})
	);
	// Brief pause for processes to die
	await sleep(1000);
}

/**
 * Form a ring and start inference.
 *
 * This is the main orchestration function that replaces the bash script.
 */
export async function startRing(
	config: RingFormationConfig,
	onProgress?: ProgressCallback
): Promise<void> {
	const { devices, totalLayers, contextSize, threads, taskset, prefetch, httpPort, dataPort, signalPort } = config;
	const nPhones = devices.length;
	const totalSteps = nPhones + 3; // kill + N workers + master + verify

	if (nPhones < 2) {
		throw new Error('Ring requires at least 2 devices');
	}

	// Verify all devices have ADB connections
	for (const d of devices) {
		if (!d.adb) throw new Error(`Device ${d.serial} is not connected`);
	}

	// Step 1: Kill existing processes
	emit(onProgress, 'killing', 'Stopping existing processes...', 1, totalSteps);
	await killAllProcesses(devices);

	// Calculate layer distribution
	const lw = calcLayerWeights(totalLayers, nPhones);
	emit(onProgress, 'killing', `Layer weights: ${lw}`, 1, totalSteps, 1);

	// Resolve IP addresses for all devices
	const ips = new Map<string, string>();
	for (const d of devices) {
		const serial = d.serial;
		// Ethernet phones: serial is IP:port format
		if (serial.match(/^\d+\.\d+\.\d+\.\d+:\d+$/)) {
			ips.set(serial, serial.split(':')[0]);
		} else {
			const ip = await getIpAddress(d.adb!);
			if (!ip) throw new Error(`Could not resolve IP for device ${serial}`);
			ips.set(serial, ip);
		}
	}

	const masterIp = ips.get(devices[0].serial)!;

	// Step 2: Start workers (rank 1 to N-1)
	emit(onProgress, 'starting-workers', `Starting ${nPhones - 1} workers...`, 2, totalSteps);

	for (let idx = 1; idx < nPhones; idx++) {
		const device = devices[idx];
		const ip = ips.get(device.serial)!;
		const nextIdx = (idx + 1) % nPhones;
		const nextIp = ips.get(devices[nextIdx].serial)!;

		const cmd = buildWorkerCommand({
			modelPath: config.modelPath,
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

		await shellCmd(device.adb!, `sh -c '${cmd}'`);
		emit(onProgress, 'starting-workers', `Worker rank ${idx} started on ${ip}`, idx + 1, totalSteps, idx / (nPhones - 1));
	}

	// Step 3: Wait for workers to load model
	emit(onProgress, 'loading-model', 'Waiting for workers to load model (this takes ~2 min)...', nPhones, totalSteps);

	const loadTimeout = 180_000; // 3 minutes
	const pollInterval = 5_000;
	const startTime = Date.now();

	while (Date.now() - startTime < loadTimeout) {
		await sleep(pollInterval);

		// Check all workers are still running
		let allRunning = true;
		for (let idx = 1; idx < nPhones; idx++) {
			const pid = await pidof(devices[idx].adb!, 'cellswarm-worker');
			if (pid === null) {
				allRunning = false;
				break;
			}
		}

		if (allRunning) {
			const elapsed = Math.round((Date.now() - startTime) / 1000);
			emit(onProgress, 'loading-model', `Workers loading... (${elapsed}s)`, nPhones, totalSteps, Math.min((Date.now() - startTime) / loadTimeout, 0.95));
		} else {
			// Some workers died — check which
			const dead: number[] = [];
			for (let idx = 1; idx < nPhones; idx++) {
				const pid = await pidof(devices[idx].adb!, 'cellswarm-worker');
				if (pid === null) dead.push(idx);
			}
			throw new Error(`Workers failed to start: ranks ${dead.join(', ')}`);
		}

		// If enough time has passed, assume model is loaded
		if (Date.now() - startTime > 120_000) break;
	}

	// Step 4: Start master (rank 0)
	emit(onProgress, 'starting-master', 'Starting master (rank 0)...', nPhones + 1, totalSteps);

	const nextIp = ips.get(devices[1].serial)!;
	const masterCmd = buildMasterCommand({
		modelPath: config.modelPath,
		draftModelPath: config.draftModelPath,
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

	await shellCmd(devices[0].adb!, `sh -c '${masterCmd}'`);
	await sleep(10_000); // Give master time to start

	// Verify master is running
	const masterPid = await pidof(devices[0].adb!, 'cellswarm-master');
	if (masterPid === null) {
		const log = await shellCmd(devices[0].adb!, 'tail -20 /data/local/tmp/cellswarm-master.log 2>/dev/null || echo "No log"');
		throw new Error(`Master failed to start. Log:\n${log}`);
	}

	// Step 5: Verify ring via HTTP
	emit(onProgress, 'verifying', 'Verifying ring health...', nPhones + 2, totalSteps);

	const verifyTimeout = 60_000;
	const verifyStart = Date.now();
	while (Date.now() - verifyStart < verifyTimeout) {
		try {
			const resp = await adbFetch(devices[0].adb!, '/api/ring', { port: httpPort });
			if (resp.ok) {
				const data = resp.json();
				if (data.n_world > 0 || data.world_size > 0) {
					emit(onProgress, 'ready', `Ring ready! ${nPhones} nodes.`, totalSteps, totalSteps, 1);
					return;
				}
			}
		} catch { /* ring not ready yet */ }
		await sleep(3000);
	}

	// If we got here, master is running but ring didn't report ready
	// This might be okay — master might still be loading its own layers
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
	modelPath: string;
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
	const modelRemote = `${REMOTE_MODEL_DIR}/${p.modelPath}`;
	const prefetchFlag = p.prefetch ? '--prefetch' : '';

	return `cd /data/local/tmp && ` +
		`taskset ${p.taskset} ./cellswarm/bin/cellswarm-worker ` +
		`-m ${modelRemote} ` +
		`--world ${p.world} --rank ${p.rank} ` +
		`--master ${p.masterIp} --next ${p.nextIp} ` +
		`--data-port ${p.dataPort} --signal-port ${p.signalPort} ` +
		`-lw ${p.lw} -c ${p.contextSize} -n -1 -t ${p.threads} -tb ${p.threads} ` +
		`--no-mmap ${prefetchFlag} ` +
		`> /data/local/tmp/cellswarm-worker.log 2>&1 &`;
}

interface MasterCmdParams extends WorkerCmdParams {
	draftModelPath?: string;
	draftMax: number;
	httpPort: number;
}

function buildMasterCommand(p: MasterCmdParams): string {
	const modelRemote = `${REMOTE_MODEL_DIR}/${p.modelPath}`;
	const prefetchFlag = p.prefetch ? '--prefetch' : '';

	let draftFlags = '';
	if (p.draftModelPath) {
		const draftRemote = `${REMOTE_MODEL_DIR}/${p.draftModelPath}`;
		draftFlags = `--model-draft ${draftRemote} --draft-max ${p.draftMax} `;
	}

	return `cd /data/local/tmp && ` +
		`SWARM_BATCH_PIPELINE=1 taskset ${p.taskset} ./cellswarm/bin/cellswarm-master ` +
		`-m ${modelRemote} ` +
		`${draftFlags}` +
		`--world ${p.world} --rank 0 ` +
		`--master ${p.masterIp} --next ${p.nextIp} ` +
		`--data-port ${p.dataPort} --signal-port ${p.signalPort} ` +
		`-lw ${p.lw} -c ${p.contextSize} -t ${p.threads} -tb ${p.threads} ` +
		`--no-mmap ${prefetchFlag} ` +
		`--host 0.0.0.0 --port ${p.httpPort} -np 1 ` +
		`> /data/local/tmp/cellswarm-master.log 2>&1 &`;
}

function sleep(ms: number): Promise<void> {
	return new Promise(resolve => setTimeout(resolve, ms));
}
