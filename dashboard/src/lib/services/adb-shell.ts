/**
 * ADB Shell Helpers — run commands and parse output via ADB.
 *
 * All functions take an Adb instance and return parsed results.
 */

import type { Adb } from '@yume-chan/adb';

/**
 * Run a shell command on the device and return stdout as string.
 */
export async function shellCmd(adb: Adb, cmd: string): Promise<string> {
	const process = await adb.subprocess.spawn(cmd);
	const chunks: string[] = [];
	const reader = process.stdout.getReader();
	const decoder = new TextDecoder();

	try {
		while (true) {
			const { done, value } = await reader.read();
			if (done) break;
			chunks.push(decoder.decode(value, { stream: true }));
		}
	} finally {
		reader.releaseLock();
	}

	// Wait for exit
	await process.exit;

	return chunks.join('').trim();
}

/**
 * Get a system property via getprop.
 */
export async function getprop(adb: Adb, prop: string): Promise<string> {
	return shellCmd(adb, `getprop ${prop}`);
}

/**
 * Get device model name.
 */
export async function getModel(adb: Adb): Promise<string> {
	return getprop(adb, 'ro.product.model');
}

/**
 * Get manufacturer.
 */
export async function getManufacturer(adb: Adb): Promise<string> {
	return getprop(adb, 'ro.product.manufacturer');
}

/**
 * Get Android version.
 */
export async function getAndroidVersion(adb: Adb): Promise<string> {
	return getprop(adb, 'ro.build.version.release');
}

/**
 * Get SDK version.
 */
export async function getSdkVersion(adb: Adb): Promise<number> {
	const v = await getprop(adb, 'ro.build.version.sdk');
	return parseInt(v) || 0;
}

/**
 * Get SoC platform (e.g. "lahaina" for Snapdragon 888).
 */
export async function getPlatform(adb: Adb): Promise<string> {
	// Try multiple props in order of specificity
	let p = await getprop(adb, 'ro.board.platform');
	if (p) return p;
	p = await getprop(adb, 'ro.hardware.chipset');
	if (p) return p;
	p = await getprop(adb, 'ro.product.board');
	return p || 'unknown';
}

/**
 * Get CPU architecture.
 */
export async function getCpuArch(adb: Adb): Promise<string> {
	return getprop(adb, 'ro.product.cpu.abi');
}

/**
 * Get number of CPU cores.
 */
export async function getCpuCores(adb: Adb): Promise<number> {
	const out = await shellCmd(adb, 'nproc');
	return parseInt(out) || 0;
}

/**
 * Parse /proc/meminfo and return RAM in MB.
 */
export async function getMemInfo(adb: Adb): Promise<{ totalMb: number; availableMb: number }> {
	const out = await shellCmd(adb, 'cat /proc/meminfo');
	const total = parseInt(out.match(/MemTotal:\s+(\d+)/)?.[1] ?? '0') / 1024;
	const available = parseInt(out.match(/MemAvailable:\s+(\d+)/)?.[1] ?? '0') / 1024;
	return { totalMb: Math.round(total), availableMb: Math.round(available) };
}

/**
 * Get available storage in GB.
 */
export async function getStorageFree(adb: Adb): Promise<number> {
	const out = await shellCmd(adb, 'df /data/local/tmp | tail -1');
	// df output: Filesystem  1K-blocks  Used  Available  Use%  Mounted on
	const parts = out.split(/\s+/);
	const availKb = parseInt(parts[3] ?? '0');
	return Math.round(availKb / 1024 / 1024 * 10) / 10; // GB with 1 decimal
}

/**
 * Get thermal temperature in Celsius.
 */
export async function getThermalTemp(adb: Adb): Promise<number> {
	try {
		// Try common thermal zone paths
		const out = await shellCmd(adb, 'cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null || echo 0');
		const raw = parseInt(out);
		// Thermal zones report in millidegrees
		return raw > 1000 ? Math.round(raw / 1000) : raw;
	} catch {
		return 0;
	}
}

/**
 * Get the device's IP address on the network.
 */
export async function getIpAddress(adb: Adb): Promise<string> {
	// Try wlan0 first, then eth0
	let out = await shellCmd(adb, "ip addr show wlan0 2>/dev/null | grep 'inet ' | awk '{print $2}' | cut -d/ -f1");
	if (out && out !== '') return out;

	out = await shellCmd(adb, "ip addr show eth0 2>/dev/null | grep 'inet ' | awk '{print $2}' | cut -d/ -f1");
	if (out && out !== '') return out;

	// Fallback: getprop for DHCP
	out = await shellCmd(adb, 'getprop dhcp.wlan0.ipaddress');
	return out || '';
}

/**
 * Check if a process is running by name.
 */
export async function pidof(adb: Adb, name: string): Promise<number | null> {
	const out = await shellCmd(adb, `pidof ${name}`);
	const pid = parseInt(out);
	return isNaN(pid) ? null : pid;
}

/**
 * Kill a process by name.
 */
export async function pkill(adb: Adb, name: string, signal = 9): Promise<void> {
	await shellCmd(adb, `pkill -${signal} ${name} 2>/dev/null || true`);
}

/**
 * Check if cellswarm binaries exist on device.
 */
export async function hasCellswarmBinaries(adb: Adb): Promise<boolean> {
	const out = await shellCmd(adb, 'ls /data/local/tmp/cellswarm/bin/cellswarm-worker 2>/dev/null && echo YES || echo NO');
	return out.includes('YES');
}

/**
 * List model files on device.
 */
export async function listModels(adb: Adb): Promise<string[]> {
	const out = await shellCmd(adb, 'ls /data/local/tmp/cellswarm/models/*.gguf 2>/dev/null || true');
	if (!out) return [];
	return out.split('\n')
		.map(line => line.trim())
		.filter(line => line.endsWith('.gguf'))
		.map(path => path.split('/').pop()!)
		.filter(Boolean);
}

/**
 * Check if cellswarm-worker or cellswarm-master is currently running.
 */
export async function hasSwarmProcess(adb: Adb): Promise<boolean> {
	const worker = await pidof(adb, 'cellswarm-worker');
	if (worker !== null) return true;
	const master = await pidof(adb, 'cellswarm-master');
	return master !== null;
}
