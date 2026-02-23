/**
 * Binary Registry — chipset detection and binary selection.
 *
 * Determines which pre-built ARM binaries to deploy based on device SoC.
 */

import type { Adb } from '@yume-chan/adb';
import type { ChipsetProfile } from '$lib/types/adb';
import { lookupChipset, getDefaultChipset } from '$lib/utils/chipset-db';
import { getPlatform } from './adb-shell';

/** Names of cellswarm binaries */
export const BINARY_NAMES = [
	'cellswarm-master',
	'cellswarm-worker',
	'cellswarm-worker-spec',
	'cellswarm-agent',
] as const;

export type BinaryName = typeof BINARY_NAMES[number];

/** Remote directory on phone where binaries live */
export const REMOTE_BIN_DIR = '/data/local/tmp/cellswarm/bin';

/** Remote directory on phone where models live */
export const REMOTE_MODEL_DIR = '/data/local/tmp/cellswarm/models';

/**
 * Detect the chipset of a connected device and return the matching profile.
 */
export async function detectChipset(adb: Adb): Promise<ChipsetProfile> {
	const platform = await getPlatform(adb);
	const profile = lookupChipset(platform);

	if (!profile) {
		console.warn(`Unknown platform "${platform}", falling back to default (Snapdragon 888)`);
		return getDefaultChipset();
	}

	return profile;
}

/**
 * Get the URL path for a specific binary for a chipset.
 * Path is relative to the static/binaries/ directory.
 */
export function getBinaryPath(chipset: ChipsetProfile, binary: BinaryName): string {
	return `${chipset.binaryPath}${binary}`;
}

/**
 * Check if all required binaries exist on the device.
 */
export async function checkBinaries(
	adb: Adb,
	binaries: BinaryName[] = ['cellswarm-master', 'cellswarm-worker']
): Promise<{ missing: BinaryName[]; present: BinaryName[] }> {
	const { shellCmd } = await import('./adb-shell');

	const results = await Promise.all(
		binaries.map(async (name) => {
			const out = await shellCmd(adb, `test -f ${REMOTE_BIN_DIR}/${name} && echo YES || echo NO`);
			return { name, present: out.includes('YES') };
		})
	);

	return {
		missing: results.filter(r => !r.present).map(r => r.name),
		present: results.filter(r => r.present).map(r => r.name),
	};
}
