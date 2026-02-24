/**
 * Device Prober — batch-collects device info via ADB shell commands.
 *
 * Runs all probes in parallel for speed.
 */

import type { Adb } from '@yume-chan/adb';
import type { DeviceInfo, ManagedDevice } from '$lib/types/adb';
import { lookupChipset, getDefaultChipset } from '$lib/utils/chipset-db';
import {
	getModel,
	getManufacturer,
	getAndroidVersion,
	getSdkVersion,
	getCpuArch,
	getCpuCores,
	getPlatform,
	getMemInfo,
	getStorageFree,
	getThermalTemp,
	getIpAddress,
	getBatteryLevel,
	hasCellswarmBinaries,
	hasSwarmProcess,
	listModels,
} from './adb-shell';

/**
 * Probe a single device for all info. Returns DeviceInfo.
 */
export async function probeDevice(adb: Adb): Promise<DeviceInfo> {
	const [
		model,
		manufacturer,
		androidVersion,
		sdkVersion,
		cpuArch,
		cpuCores,
		platform,
		mem,
		storageFreeGb,
		thermalTempC,
		ipAddress,
		batteryLevel,
		hasBinaries,
		hasSwarm,
	] = await Promise.all([
		getModel(adb),
		getManufacturer(adb),
		getAndroidVersion(adb),
		getSdkVersion(adb),
		getCpuArch(adb),
		getCpuCores(adb),
		getPlatform(adb),
		getMemInfo(adb),
		getStorageFree(adb),
		getThermalTemp(adb),
		getIpAddress(adb),
		getBatteryLevel(adb),
		hasCellswarmBinaries(adb),
		hasSwarmProcess(adb),
	]);

	const chipsetProfile = lookupChipset(platform);

	return {
		model,
		manufacturer,
		androidVersion,
		sdkVersion,
		cpuArch,
		cpuCores,
		platform,
		chipset: chipsetProfile?.name ?? `Unknown (${platform})`,
		totalRamMb: mem.totalMb,
		availableRamMb: mem.availableMb,
		usableRamMb: Math.max(0, mem.availableMb - 500), // Reserve 500MB for OS
		storageFreeGb,
		thermalTempC,
		batteryLevel,
		ipAddress,
		hasBinaries,
		hasSwarmProcess: hasSwarm,
	};
}

/**
 * Probe multiple devices in parallel.
 */
export async function probeAllDevices(devices: ManagedDevice[]): Promise<Map<string, DeviceInfo>> {
	const results = new Map<string, DeviceInfo>();

	const promises = devices
		.filter(d => d.adb !== null)
		.map(async (d) => {
			try {
				const info = await probeDevice(d.adb!);
				results.set(d.serial, info);
			} catch (e) {
				console.warn(`Probe failed for ${d.serial}:`, e);
			}
		});

	await Promise.all(promises);
	return results;
}

/**
 * Quick refresh — only updates volatile data (RAM, thermal, processes).
 */
export async function quickProbe(adb: Adb): Promise<Partial<DeviceInfo>> {
	const [mem, thermalTempC, batteryLevel, hasSwarm] = await Promise.all([
		getMemInfo(adb),
		getThermalTemp(adb),
		getBatteryLevel(adb),
		hasSwarmProcess(adb),
	]);

	return {
		availableRamMb: mem.availableMb,
		usableRamMb: Math.max(0, mem.availableMb - 500),
		thermalTempC,
		batteryLevel,
		hasSwarmProcess: hasSwarm,
	};
}
