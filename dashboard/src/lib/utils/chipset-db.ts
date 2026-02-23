/**
 * SoC platform → chipset profile mapping.
 * Used to select correct ARM binaries for each phone.
 */

import type { ChipsetProfile } from '$lib/types/adb';

const CHIPSET_DB: ChipsetProfile[] = [
	{
		id: 'sm8350',
		name: 'Snapdragon 888',
		aliases: ['lahaina', 'SM8350'],
		binaryPath: 'sm8350/',
		threads: 4,
		taskset: 'f0', // cores 4-7 (big cores)
		buildFlags: ['-march=armv8.2-a+dotprod+fp16'],
	},
	{
		id: 'sm8450',
		name: 'Snapdragon 8 Gen 1',
		aliases: ['taro', 'SM8450'],
		binaryPath: 'sm8450/',
		threads: 4,
		taskset: 'f0',
		buildFlags: ['-march=armv8.4-a+dotprod+fp16+i8mm'],
	},
	{
		id: 'sm8550',
		name: 'Snapdragon 8 Gen 2',
		aliases: ['kalama', 'SM8550'],
		binaryPath: 'sm8550/',
		threads: 4,
		taskset: 'f0',
		buildFlags: ['-march=armv8.4-a+dotprod+fp16+i8mm'],
	},
	{
		id: 'gs101',
		name: 'Google Tensor',
		aliases: ['slider', 'gs101'],
		binaryPath: 'gs101/',
		threads: 4,
		taskset: 'f0',
		buildFlags: ['-march=armv8.2-a+dotprod+fp16'],
	},
	{
		id: 'exynos2100',
		name: 'Exynos 2100',
		aliases: ['exynos2100', 'universal2100'],
		binaryPath: 'exynos2100/',
		threads: 4,
		taskset: 'f0',
		buildFlags: ['-march=armv8.2-a+dotprod+fp16'],
	},
];

/**
 * Look up chipset profile from a platform string returned by `getprop ro.board.platform`.
 */
export function lookupChipset(platform: string): ChipsetProfile | null {
	const p = platform.toLowerCase().trim();
	for (const profile of CHIPSET_DB) {
		if (profile.id === p) return profile;
		for (const alias of profile.aliases) {
			if (alias.toLowerCase() === p) return profile;
		}
	}
	return null;
}

/**
 * Get all known chipset profiles.
 */
export function getAllChipsets(): ChipsetProfile[] {
	return CHIPSET_DB;
}

/**
 * Get the default (Snapdragon 888) profile — our primary target.
 */
export function getDefaultChipset(): ChipsetProfile {
	return CHIPSET_DB[0];
}
