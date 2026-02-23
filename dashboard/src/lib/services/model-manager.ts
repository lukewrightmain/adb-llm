/**
 * Model Manager — scan, download, and manage GGUF models on devices.
 */

import type { Adb } from '@yume-chan/adb';
import type { ManagedDevice } from '$lib/types/adb';
import { shellCmd, listModels } from './adb-shell';
import { REMOTE_MODEL_DIR } from './binary-registry';

export interface ModelOnDevice {
	name: string;
	sizeMb: number;
	path: string;
}

/**
 * List all GGUF models on a device with sizes.
 */
export async function scanModels(adb: Adb): Promise<ModelOnDevice[]> {
	const out = await shellCmd(adb, `ls -l ${REMOTE_MODEL_DIR}/*.gguf 2>/dev/null || true`);
	if (!out) return [];

	const models: ModelOnDevice[] = [];
	for (const line of out.split('\n')) {
		// -rw-rw-rw- 1 shell shell 19853611008 ... deepseek-coder-33b-instruct.Q4_K_M.gguf
		const match = line.match(/\s(\d+)\s+\S+\s+\S+\s+(\S+\.gguf)$/);
		if (match) {
			const sizeBytes = parseInt(match[1]);
			const name = match[2];
			models.push({
				name,
				sizeMb: Math.round(sizeBytes / 1024 / 1024),
				path: `${REMOTE_MODEL_DIR}/${name}`,
			});
		}
	}

	return models;
}

/**
 * Download a model to the device using wget.
 * The phone itself fetches the model over its network connection.
 *
 * @returns Background PID on the device
 */
export async function downloadModelOnDevice(
	adb: Adb,
	url: string,
	filename: string
): Promise<number> {
	await shellCmd(adb, `mkdir -p ${REMOTE_MODEL_DIR}`);
	const remotePath = `${REMOTE_MODEL_DIR}/${filename}`;
	const out = await shellCmd(adb, `wget -q -O ${remotePath} '${url}' > /dev/null 2>&1 & echo $!`);
	const pid = parseInt(out);
	if (isNaN(pid)) throw new Error('Failed to start download');
	return pid;
}

/**
 * Check download progress by watching file size vs expected.
 */
export async function checkDownloadProgress(
	adb: Adb,
	filename: string,
	expectedSizeBytes?: number
): Promise<{ downloading: boolean; sizeBytes: number; progress: number }> {
	const remotePath = `${REMOTE_MODEL_DIR}/${filename}`;
	const out = await shellCmd(adb, `stat -c '%s' ${remotePath} 2>/dev/null || echo 0`);
	const sizeBytes = parseInt(out) || 0;

	// Check if wget is still running
	const wgetPid = await shellCmd(adb, 'pidof wget 2>/dev/null || echo 0');
	const downloading = parseInt(wgetPid) > 0;

	const progress = expectedSizeBytes ? Math.min(sizeBytes / expectedSizeBytes, 1) : 0;

	return { downloading, sizeBytes, progress };
}

/**
 * Delete a model from the device.
 */
export async function deleteModel(adb: Adb, filename: string): Promise<void> {
	await shellCmd(adb, `rm -f ${REMOTE_MODEL_DIR}/${filename}`);
}

/**
 * Scan models across multiple devices.
 */
export async function scanAllDeviceModels(
	devices: ManagedDevice[]
): Promise<Map<string, ModelOnDevice[]>> {
	const results = new Map<string, ModelOnDevice[]>();

	await Promise.all(
		devices
			.filter(d => d.adb)
			.map(async (d) => {
				try {
					const models = await scanModels(d.adb!);
					results.set(d.serial, models);
				} catch {
					results.set(d.serial, []);
				}
			})
	);

	return results;
}
