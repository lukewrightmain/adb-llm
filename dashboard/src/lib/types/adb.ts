/**
 * ADB-specific types for the WebUSB dashboard.
 */

import type { Adb } from '@yume-chan/adb';

export interface ManagedDevice {
	/** ADB serial string (e.g. "10.105.0.12:5555" for ethernet, "R3CR904AQKA" for USB) */
	serial: string;
	/** Shortened serial for display */
	shortSerial: string;
	/** Live ADB connection (null when disconnected) */
	adb: Adb | null;
	/** USB device reference for reconnection */
	usbDevice: USBDevice | null;
	/** Connection state */
	state: DeviceState;
	/** Device info (populated after probing) */
	info: DeviceInfo | null;
	/** Ring assignment */
	ringRank: number | null;
	/** Available model files on device */
	models: string[];
	/** Last error message */
	lastError: string | null;
	/** Timestamp of last successful probe */
	lastProbeAt: number;
}

export type DeviceState = 'disconnected' | 'connecting' | 'connected' | 'probing' | 'ready' | 'busy' | 'error';

export interface DeviceInfo {
	model: string;
	manufacturer: string;
	androidVersion: string;
	sdkVersion: number;
	cpuArch: string;
	cpuCores: number;
	/** SoC platform (e.g. "lahaina" for Snapdragon 888) */
	platform: string;
	/** Resolved chipset name */
	chipset: string;
	totalRamMb: number;
	availableRamMb: number;
	usableRamMb: number;
	storageFreeGb: number;
	thermalTempC: number;
	/** IP address on network (for ring formation) */
	ipAddress: string;
	/** Whether cellswarm binaries exist on device */
	hasBinaries: boolean;
	/** Whether cellswarm-worker or cellswarm-master is running */
	hasSwarmProcess: boolean;
}

export interface ChipsetProfile {
	/** Internal ID (e.g. "sm8350") */
	id: string;
	/** Human-readable name */
	name: string;
	/** Platform aliases returned by getprop (e.g. ["lahaina"]) */
	aliases: string[];
	/** Path relative to static/binaries/ */
	binaryPath: string;
	/** Optimal thread count */
	threads: number;
	/** Taskset mask for big cores */
	taskset: string;
	/** CPU build flags */
	buildFlags: string[];
}

export interface BinaryManifest {
	[chipsetId: string]: ChipsetProfile;
}

export interface RingFormationConfig {
	/** Devices to include in ring (ordered: index 0 = master) */
	devices: ManagedDevice[];
	/** Model GGUF path on device */
	modelPath: string;
	/** Draft model GGUF path on device (optional) */
	draftModelPath?: string;
	/** Max speculative tokens */
	draftMax: number;
	/** Total transformer layers in model */
	totalLayers: number;
	/** Context window size */
	contextSize: number;
	/** Thread count per device */
	threads: number;
	/** Taskset mask */
	taskset: string;
	/** Enable --prefetch */
	prefetch: boolean;
	/** HTTP port on master */
	httpPort: number;
	/** Data port for ring communication */
	dataPort: number;
	/** Signal port for ring communication */
	signalPort: number;
}

export type RingFormationPhase =
	| 'idle'
	| 'killing'
	| 'deploying'
	| 'starting-workers'
	| 'loading-model'
	| 'starting-master'
	| 'verifying'
	| 'ready'
	| 'error';

export interface RingFormationProgress {
	phase: RingFormationPhase;
	message: string;
	/** 0-1 progress within current phase */
	progress: number;
	/** Current step / total steps */
	step: number;
	totalSteps: number;
}
