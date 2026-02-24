/**
 * ADB Shell Helpers — run commands and parse output via ADB.
 *
 * All functions take an Adb instance and return parsed results.
 * Uses ya-webadb's subprocess.noneProtocol for shell commands.
 */

import type { Adb } from '@yume-chan/adb';

/**
 * Run a shell command on the device and return stdout as string.
 * Uses the "none" protocol (legacy shell) which works on all Android versions.
 */
export async function shellCmd(adb: Adb, cmd: string): Promise<string> {
	return adb.subprocess.noneProtocol.spawnWaitText(cmd);
}

/**
 * Get a system property via getprop.
 */
export async function getprop(adb: Adb, prop: string): Promise<string> {
	// Use Adb's built-in getProp which handles escaping
	return adb.getProp(prop);
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
 * Get battery level (0-100).
 */
export async function getBatteryLevel(adb: Adb): Promise<number> {
	try {
		const out = await shellCmd(adb, 'dumpsys battery | grep level');
		const match = out.match(/level:\s*(\d+)/);
		return match ? parseInt(match[1]) : 0;
	} catch {
		return 0;
	}
}

/**
 * Set battery level to a specific value via dumpsys.
 */
export async function setBatteryLevel(adb: Adb, level: number): Promise<void> {
	await shellCmd(adb, `dumpsys battery set level ${level}`);
}

/**
 * Reset battery stats to real values (undo any set level).
 */
export async function resetBattery(adb: Adb): Promise<void> {
	await shellCmd(adb, 'dumpsys battery reset');
}

/**
 * Check if cellswarm binaries exist on device.
 */
export async function hasCellswarmBinaries(adb: Adb): Promise<boolean> {
	const out = await shellCmd(adb, 'ls /data/local/tmp/cellswarm/bin/cellswarm-worker 2>/dev/null && echo YES || echo NO');
	return out.includes('YES');
}

/**
 * List model files on device. Searches multiple directories and file types.
 */
export async function listModels(adb: Adb): Promise<string[]> {
	const dirs = '/data/local/tmp/cellswarm/models /data/local/tmp/adb-llm/models /data/local/tmp/models';
	const out = await shellCmd(adb, `find ${dirs} -maxdepth 1 \\( -name "*.gguf" -o -name "*.bin" -o -name "*.safetensors" \\) 2>/dev/null || true`);
	if (!out) return [];
	const seen = new Set<string>();
	return out.split('\n')
		.map(line => line.trim())
		.filter(line => line.length > 0 && line.startsWith('/'))
		.map(path => path.split('/').pop()!)
		.filter(name => {
			if (!name || seen.has(name)) return false;
			seen.add(name);
			return true;
		});
}

/** Bloatware packages safe to force-stop */
const BLOAT_PACKAGES = [
	// Samsung
	'com.samsung.android.game.gamehome',
	'com.samsung.android.game.gametools',
	'com.samsung.android.bixby.agent',
	'com.samsung.android.bixby.service',
	'com.samsung.android.visionintelligence',
	'com.samsung.android.ardrawing',
	'com.samsung.android.arzone',
	'com.samsung.android.app.tips',
	'com.samsung.android.app.reminder',
	'com.samsung.android.calendar',
	'com.samsung.android.email.provider',
	'com.samsung.android.mobileservice',
	'com.samsung.android.samsungpass',
	'com.samsung.android.spay',
	'com.samsung.android.forest',
	'com.samsung.android.wellbeing',
	'com.samsung.android.app.spage',
	'com.samsung.android.app.news',
	'com.samsung.android.themestore',
	'com.samsung.android.app.dressroom',
	'com.samsung.android.livestickers',
	'com.samsung.android.stickercenter',
	'com.samsung.android.app.routines',
	'com.samsung.android.smartsuggestions',
	// Google non-essential
	'com.google.android.apps.magazines',
	'com.google.android.apps.tachyon',
	'com.google.android.apps.photos',
	'com.google.android.apps.docs',
	'com.google.android.apps.maps',
	'com.google.android.youtube',
	'com.google.android.music',
	'com.google.android.videos',
	'com.google.android.apps.youtube.music',
	'com.google.android.gm',
	'com.google.android.calendar',
	'com.google.android.keep',
	// Other common heavy apps
	'com.facebook.katana',
	'com.facebook.orca',
	'com.instagram.android',
	'com.whatsapp',
	'com.spotify.music',
	'com.netflix.mediaclient',
	'com.twitter.android',
	'com.snapchat.android',
	'com.tiktok.android',
];

/**
 * Safe RAM cleanup — kills background/cached apps and Samsung bloatware.
 * Does NOT touch system-critical processes, cellswarm binaries, or ADB.
 *
 * Sends everything as a single shell command to avoid round-trip overhead.
 * Returns freed MB (approximate, measured before/after).
 */
export async function cleanRam(adb: Adb): Promise<{ freedMb: number }> {
	// Measure RAM before
	const before = await getMemInfo(adb);

	// Build one big shell script: kill-all + all force-stops + drop caches + trim
	// Each command has its own error suppression so failures don't stop the chain
	const forceStops = BLOAT_PACKAGES
		.map(pkg => `am force-stop ${pkg}`)
		.join('; ');

	const script = [
		'am kill-all',
		forceStops,
		'echo 3 > /proc/sys/vm/drop_caches',
		'pm trim-caches 512M',
	].join('; ');

	// Run entire cleanup as single command with timeout wrapper
	// timeout command kills it after 12s if it hangs on any package
	await shellCmd(adb, `timeout 12 sh -c '${script.replace(/'/g, "'\\''")}' 2>/dev/null; echo DONE`);

	// Measure RAM after
	const after = await getMemInfo(adb);
	const freedMb = Math.max(0, after.availableMb - before.availableMb);

	return { freedMb };
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
