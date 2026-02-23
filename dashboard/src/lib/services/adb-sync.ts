/**
 * ADB File Sync — push files to device via ADB.
 *
 * Uses adb shell + base64 for small files, or AdbSync for large files.
 */

import type { Adb } from '@yume-chan/adb';
import { shellCmd } from './adb-shell';
import { REMOTE_BIN_DIR, REMOTE_MODEL_DIR } from './binary-registry';

/**
 * Ensure a directory exists on the device.
 */
export async function mkdirp(adb: Adb, path: string): Promise<void> {
	await shellCmd(adb, `mkdir -p ${path}`);
}

/**
 * Push a binary file to the device using AdbSync.
 * Returns the remote path.
 */
export async function pushFile(
	adb: Adb,
	data: Uint8Array,
	remotePath: string,
	onProgress?: (sent: number, total: number) => void
): Promise<string> {
	// Ensure parent directory exists
	const dir = remotePath.substring(0, remotePath.lastIndexOf('/'));
	await mkdirp(adb, dir);

	const sync = await adb.sync();
	try {
		// Create a ReadableStream from the data
		const readable = new ReadableStream<Uint8Array>({
			start(controller) {
				// Send in chunks for progress reporting
				const CHUNK_SIZE = 64 * 1024;
				let offset = 0;
				while (offset < data.length) {
					const chunk = data.slice(offset, offset + CHUNK_SIZE);
					controller.enqueue(chunk);
					offset += chunk.length;
					onProgress?.(offset, data.length);
				}
				controller.close();
			}
		});

		await sync.write({
			filename: remotePath,
			file: readable,
			type: 'file',
			permission: 0o755,
		});
	} finally {
		await sync.dispose();
	}

	return remotePath;
}

/**
 * Push a cellswarm binary to the device.
 * Fetches from static/binaries/ in the dashboard, pushes to phone.
 */
export async function pushBinary(
	adb: Adb,
	binaryUrl: string,
	binaryName: string,
	onProgress?: (sent: number, total: number) => void
): Promise<string> {
	// Fetch binary from the dashboard's static files
	const resp = await fetch(binaryUrl);
	if (!resp.ok) {
		throw new Error(`Failed to fetch binary: ${resp.status} ${resp.statusText}`);
	}
	const data = new Uint8Array(await resp.arrayBuffer());

	const remotePath = `${REMOTE_BIN_DIR}/${binaryName}`;
	await pushFile(adb, data, remotePath, onProgress);

	// Make executable
	await shellCmd(adb, `chmod +x ${remotePath}`);

	return remotePath;
}

/**
 * Check if a file exists on device and return its size.
 */
export async function statFile(adb: Adb, path: string): Promise<{ exists: boolean; sizeBytes: number }> {
	const out = await shellCmd(adb, `stat -c '%s' ${path} 2>/dev/null || echo NOTFOUND`);
	if (out.includes('NOTFOUND')) return { exists: false, sizeBytes: 0 };
	return { exists: true, sizeBytes: parseInt(out) || 0 };
}
