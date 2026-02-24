/**
 * Model Manager — scan, download, and manage AI models on devices.
 *
 * Searches multiple directories and file types (.gguf, .bin, .safetensors, etc.)
 */

import type { Adb } from '@yume-chan/adb';
import type { ManagedDevice } from '$lib/types/adb';
import { shellCmd, listModels } from './adb-shell';
import { REMOTE_MODEL_DIR } from './binary-registry';

export interface ModelOnDevice {
	name: string;
	sizeMb: number;
	path: string;
	/** Directory the model was found in */
	dir: string;
	/** File extension */
	ext: string;
}

/** Directories to search for model files */
const MODEL_SEARCH_DIRS = [
	'/data/local/tmp/cellswarm/models',
	'/data/local/tmp/adb-llm/models',
	'/data/local/tmp/models',
	'/sdcard/Download',
	'/sdcard/Models',
	'/data/local/tmp',
];

/** File extensions recognized as AI model files */
const MODEL_EXTENSIONS = ['gguf', 'bin', 'safetensors', 'onnx', 'pt', 'pth', 'ggml'];

/**
 * Scan all known directories for AI model files on a device.
 * Deduplicates by full path.
 */
export async function scanModels(adb: Adb): Promise<ModelOnDevice[]> {
	// Build a single find command that searches all dirs for all extensions
	const nameArgs = MODEL_EXTENSIONS
		.map((ext, i) => `${i > 0 ? '-o ' : ''}-name "*.${ext}"`)
		.join(' ');
	const dirs = MODEL_SEARCH_DIRS.join(' ');
	const cmd = `find ${dirs} -maxdepth 2 \\( ${nameArgs} \\) -exec ls -l {} \\; 2>/dev/null || true`;

	const out = await shellCmd(adb, cmd);
	if (!out) return [];

	const models: ModelOnDevice[] = [];
	const seenPaths = new Set<string>();

	for (const line of out.split('\n')) {
		if (!line.trim()) continue;
		// ls -l output: -rw-rw-rw- 1 shell shell 19853611008 2026-01-15 12:00 /full/path/to/model.gguf
		// Match: size field + full path at end
		const match = line.match(/\s(\d{6,})\s+\S+\s+\S+\s+(\/\S+)$/);
		if (match) {
			const sizeBytes = parseInt(match[1]);
			const fullPath = match[2];

			if (seenPaths.has(fullPath)) continue;
			seenPaths.add(fullPath);

			const name = fullPath.split('/').pop()!;
			const dir = fullPath.substring(0, fullPath.lastIndexOf('/'));
			const ext = name.split('.').pop() ?? '';

			models.push({
				name,
				sizeMb: Math.round(sizeBytes / 1024 / 1024),
				path: fullPath,
				dir,
				ext,
			});
		}
	}

	// Sort: largest first (primary models first), then alphabetically
	models.sort((a, b) => b.sizeMb - a.sizeMb || a.name.localeCompare(b.name));
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
 * Delete a model from the device by full path.
 */
export async function deleteModel(adb: Adb, filePath: string): Promise<void> {
	// Accept full path or just filename (backwards compat)
	const rmPath = filePath.startsWith('/') ? filePath : `${REMOTE_MODEL_DIR}/${filePath}`;
	await shellCmd(adb, `rm -f '${rmPath}'`);
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
