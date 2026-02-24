/**
 * AppStore — Central state for the CellSwarm WebUSB Dashboard.
 *
 * All device management happens directly in the browser via WebUSB + ADB.
 * No backend server required. Chat streams via HTTP over ADB sockets.
 */

import { browser } from '$app/environment';
import type {
	Device,
	RingStatus,
	RingNode,
	ChatMessage,
	Conversation,
	DeviceGroup,
	TabId,
	RingHealth,
	RingSettings,
	SpecStats,
} from '$lib/types';
import type {
	ManagedDevice,
	DeviceState,
	RingFormationConfig,
	RingFormationProgress,
} from '$lib/types/adb';
import {
	isWebUsbSupported,
	initUsbManager,
	requestDevice,
	getPairedDevices,
	connectDevice,
	disconnectDevice,
	createManagedDevice,
	createTcpManagedDevice,
	onUsbConnect,
	onUsbDisconnect,
} from '$lib/services/adb-manager';
import {
	connectTcpDevice,
	saveTcpDevice,
	removeTcpDevice,
	loadTcpDevices,
	isProxyAvailable,
	scanNetwork,
	type ScanProgress,
} from '$lib/services/adb-tcp';
import { probeDevice, quickProbe } from '$lib/services/device-prober';
import { adbFetch, adbFetchSSE } from '$lib/services/adb-http';
import { startRing as orchestrateRing, stopRing as orchestrateStopRing, killAllProcesses, configureProxy, findFreePorts } from '$lib/services/ring-orchestrator';
import { scanModels, type ModelOnDevice } from '$lib/services/model-manager';
import { listModels } from '$lib/services/adb-shell';

// ─── State ───────────────────────────────────────────────────────────

let managedDevices = $state<ManagedDevice[]>([]);
let webUsbSupported = $state(false);
let tcpProxyAvailable = $state(false);
let ringActive = $state(false);
let ringForming = $state(false);
let ringHealth = $state<RingHealth>({ ready: false, status: 'inactive' });
let ringFormationProgress = $state<RingFormationProgress | null>(null);
let ringMasterDevice = $state<ManagedDevice | null>(null);
let ringHttpPort = $state(8080);

// Chat
let conversations = $state<Conversation[]>([]);
let activeConversationId = $state<string | null>(null);
let streaming = $state(false);
let streamingContent = $state('');
let abortController = $state<AbortController | null>(null);

// Live streaming stats (updated each token for real-time display)
let streamingTokenCount = $state(0);
let streamingStartTime = $state(0);
let streamingTtftMs = $state(0);
let streamingTps = $state(0);

// Device selection & groups
let selectedSerials = $state<Set<string>>(new Set());
let deviceGroups = $state<DeviceGroup[]>([]);
let activeGroupId = $state<string | null>(null);

// Models
let deviceModels = $state<Map<string, ModelOnDevice[]>>(new Map());

// Tab state
let activeTab = $state<TabId>('devices');

// UI state
let globalError = $state<string | null>(null);
let isRefreshing = $state(false);

// Network scan state
let scanProgress = $state<ScanProgress | null>(null);
let scanAbortController = $state<AbortController | null>(null);

// Settings (persisted in localStorage)
const DEFAULT_SETTINGS: RingSettings = {
	speculative: true,
	draftMax: 24,
	totalLayers: 62,
	contextSize: 2048,
	prefetch: true,
	defaultModel: '',
	defaultDraftModel: '',
	adbPath: '',
	dataPort: 9100,
	signalPort: 10100,
	chatTemplate: '',
	seed: -1,
	temperature: 0.7,
	topK: 40,
	topP: 0.9,
	minP: 0.0,
	repeatPenalty: 1.1,
	repeatLastN: 64,
	frequencyPenalty: 0.0,
	presencePenalty: 0.0,
	maxTokens: 2048,
};
let settings = $state<RingSettings>({ ...DEFAULT_SETTINGS });

// Polling
let pollInterval: ReturnType<typeof setInterval> | null = null;
let healthInterval: ReturnType<typeof setInterval> | null = null;

const STORAGE_KEY = 'cellswarm-conversations';
const GROUPS_STORAGE_KEY = 'cellswarm-device-groups';
const SETTINGS_STORAGE_KEY = 'cellswarm-settings';
const RING_STATE_STORAGE_KEY = 'cellswarm-ring-state';
const POLL_MS = 3000;
const HEALTH_POLL_MS = 3000;

// ─── Logging ──────────────────────────────────────────────────────────
const LOG_PREFIX = '[CellSwarm]';
function log(...args: unknown[]) { console.log(LOG_PREFIX, ...args); }
function logWarn(...args: unknown[]) { console.warn(LOG_PREFIX, ...args); }
function logErr(...args: unknown[]) { console.error(LOG_PREFIX, ...args); }

// Event listener cleanup
let cleanupUsbConnect: (() => void) | null = null;
let cleanupUsbDisconnect: (() => void) | null = null;

// ─── Getters ─────────────────────────────────────────────────────────

export function getManagedDevices() { return managedDevices; }
export function getWebUsbSupported() { return webUsbSupported; }
export function getTcpProxyAvailable() { return tcpProxyAvailable; }
export function isRingActive() { return ringActive; }
export function isRingForming() { return ringForming; }
export function getRingHealth() { return ringHealth; }
export function getRingFormationProgress() { return ringFormationProgress; }
export function getRingMasterDevice() { return ringMasterDevice; }
export function getConversations() { return conversations; }
export function getActiveConversationId() { return activeConversationId; }
export function isStreaming() { return streaming; }
export function getStreamingContent() { return streamingContent; }
export function getStreamingStats() { return { tokenCount: streamingTokenCount, ttftMs: streamingTtftMs, tps: streamingTps }; }
export function getActiveTab() { return activeTab; }
export function getGlobalError() { return globalError; }
export function getIsRefreshing() { return isRefreshing; }
export function getScanProgress() { return scanProgress; }
export function isScanning() { return scanAbortController !== null; }
export function getSelectedSerials() { return selectedSerials; }
export function getDeviceGroups() { return deviceGroups; }
export function getActiveGroupId() { return activeGroupId; }
export function getSettings() { return settings; }
export function getDeviceModels() { return deviceModels; }

export function getActiveConversation(): Conversation | undefined {
	return conversations.find((c) => c.id === activeConversationId);
}

export function getReadyDevices(): ManagedDevice[] {
	return managedDevices.filter(d => d.state === 'ready');
}

export function getConnectedDevices(): ManagedDevice[] {
	return managedDevices.filter(d => d.adb !== null);
}

// ─── Tab ─────────────────────────────────────────────────────────────

export function setActiveTab(tab: TabId) {
	activeTab = tab;
}

// ─── Settings ────────────────────────────────────────────────────────

function loadSettings() {
	if (!browser) return;
	try {
		const raw = localStorage.getItem(SETTINGS_STORAGE_KEY);
		if (raw) settings = { ...DEFAULT_SETTINGS, ...JSON.parse(raw) };
	} catch { /* ignore */ }
}

function saveSettings() {
	if (!browser) return;
	localStorage.setItem(SETTINGS_STORAGE_KEY, JSON.stringify(settings));
}

export function updateSettings(partial: Partial<RingSettings>) {
	settings = { ...settings, ...partial };
	saveSettings();
}

// ─── Ring State Persistence ──────────────────────────────────────────

interface PersistedRingState {
	masterSerial: string;
	masterIp: string;
	httpPort: number;
	/** Serials of all devices in the ring */
	deviceSerials: string[];
	startedAt: number;
}

function saveRingState() {
	if (!browser || !ringMasterDevice) return;
	const state: PersistedRingState = {
		masterSerial: ringMasterDevice.serial,
		masterIp: ringMasterDevice.info?.ipAddress?.trim() || '',
		httpPort: ringHttpPort ?? 8080,
		deviceSerials: managedDevices.filter(d => d.ringRank !== null).map(d => d.serial),
		startedAt: Date.now(),
	};
	localStorage.setItem(RING_STATE_STORAGE_KEY, JSON.stringify(state));
}

function clearRingState() {
	if (!browser) return;
	localStorage.removeItem(RING_STATE_STORAGE_KEY);
}

function loadRingState(): PersistedRingState | null {
	if (!browser) return null;
	try {
		const raw = localStorage.getItem(RING_STATE_STORAGE_KEY);
		if (!raw) return null;
		return JSON.parse(raw);
	} catch { return null; }
}

/**
 * On page load, check if a ring was running before refresh.
 * Probe the master via the proxy — if still alive, restore ring state.
 */
async function tryResumeRing() {
	const saved = loadRingState();
	if (!saved || !saved.masterIp || !saved.masterSerial) {
		clearRingState();
		return;
	}

	log(`tryResumeRing: found saved ring state — master=${saved.masterSerial}, ip=${saved.masterIp}, port=${saved.httpPort}`);

	// Probe the master's /api/ring endpoint via the proxy
	try {
		const cmd = `curl -s --connect-timeout 3 http://${saved.masterIp}:${saved.httpPort}/api/ring 2>/dev/null`;
		const resp = await fetch(`http://${location.hostname}:3002/shell`, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ serial: saved.masterSerial, cmd }),
		});
		const result = await resp.json();

		if (result.ok && result.stdout?.trim()) {
			const data = JSON.parse(result.stdout.trim());
			const ready = (data.n_world > 0 || data.world_size > 0);

			if (ready) {
				log(`tryResumeRing: ring STILL ALIVE — n_world=${data.n_world}, world_size=${data.world_size}`);

				// Restore ring state — but DON'T mark health as ready yet.
				// Chat requires a live ya-webadb ADB connection to the master, which
				// only becomes available after connectAndProbe completes.
				// We set status='reconnecting' and let connectAndProbe's sync handler
				// flip it to 'ready' once the master has a live adb object.
				ringActive = true;
				ringHttpPort = saved.httpPort;
				ringHealth = { ready: false, status: 'reconnecting' };

				// Find the master device in managedDevices or create a placeholder
				const masterDev = managedDevices.find(d => d.serial === saved.masterSerial);
				if (masterDev) {
					ringMasterDevice = { ...masterDev };
				} else {
					// Master device hasn't been reconnected yet — create minimal placeholder
					const isIp = saved.masterSerial.includes(':');
					ringMasterDevice = {
						serial: saved.masterSerial,
						shortSerial: saved.masterSerial.replace(':5555', ''),
						adb: null,
						usbDevice: null,
						transport: isIp ? 'tcp' : 'usb',
						tcpHost: isIp ? saved.masterSerial.split(':')[0] : undefined,
						tcpPort: isIp ? parseInt(saved.masterSerial.split(':')[1]) || 5555 : undefined,
						state: 'connecting',
						info: { ipAddress: saved.masterIp } as any,
						ringRank: 0,
						models: [],
						lastError: null,
						lastProbeAt: 0,
					};
				}

				// Restore ring ranks on devices
				for (let i = 0; i < saved.deviceSerials.length; i++) {
					managedDevices = managedDevices.map(d =>
						d.serial === saved.deviceSerials[i]
							? { ...d, ringRank: i }
							: d
					);
				}

				// Fetch chat template from running master
				fetchChatTemplate().catch(() => {});

				log('tryResumeRing: ring state RESTORED — ready to chat');
				return;
			}
		}

		// Ring is no longer running
		log('tryResumeRing: ring is NOT running, clearing saved state');
		clearRingState();
	} catch (e) {
		log(`tryResumeRing: probe failed (${e}), clearing saved state`);
		clearRingState();
	}
}

// ─── Error ───────────────────────────────────────────────────────────

export function setGlobalError(msg: string | null) {
	globalError = msg;
}

export function dismissError() {
	globalError = null;
}

// ─── Device Selection ────────────────────────────────────────────────

export function toggleDeviceSelection(serial: string) {
	const next = new Set(selectedSerials);
	if (next.has(serial)) next.delete(serial);
	else next.add(serial);
	selectedSerials = next;
	activeGroupId = null;
}

export function selectAllDevices() {
	selectedSerials = new Set(managedDevices.map((d) => d.serial));
	activeGroupId = null;
}

export function clearSelection() {
	selectedSerials = new Set();
	activeGroupId = null;
}

export function isDeviceSelected(serial: string): boolean {
	return selectedSerials.has(serial);
}

// ─── Device Groups ───────────────────────────────────────────────────

function loadGroups() {
	if (!browser) return;
	try {
		const raw = localStorage.getItem(GROUPS_STORAGE_KEY);
		if (raw) deviceGroups = JSON.parse(raw);
	} catch { /* ignore */ }
}

function saveGroups() {
	if (!browser) return;
	localStorage.setItem(GROUPS_STORAGE_KEY, JSON.stringify(deviceGroups));
}

export function createGroup(name: string): string {
	const id = generateId();
	deviceGroups = [...deviceGroups, { id, name, serials: Array.from(selectedSerials), createdAt: Date.now() }];
	saveGroups();
	return id;
}

export function deleteGroup(id: string) {
	deviceGroups = deviceGroups.filter((g) => g.id !== id);
	if (activeGroupId === id) activeGroupId = null;
	saveGroups();
}

export function loadGroup(id: string) {
	const group = deviceGroups.find((g) => g.id === id);
	if (group) {
		selectedSerials = new Set(group.serials);
		activeGroupId = id;
	}
}

// ─── WebUSB Device Management ────────────────────────────────────────

/**
 * Initialize WebUSB + TCP proxy and try to reconnect to previously paired devices.
 */
export async function initWebUsb() {
	webUsbSupported = isWebUsbSupported();

	// Check TCP proxy availability (for ethernet/network ADB devices)
	isProxyAvailable().then(async (available) => {
		tcpProxyAvailable = available;

		// Auto-reconnect saved TCP devices
		if (available) {
			const savedTcpDevices = loadTcpDevices();
			for (const entry of savedTcpDevices) {
				const serial = `${entry.host}:${entry.port}`;
				if (managedDevices.find(d => d.serial === serial)) continue;

				const managed = createTcpManagedDevice(entry.host, entry.port);
				managedDevices = [...managedDevices, managed];
				connectAndProbe(managed.serial).catch(() => {});
			}

			// Try to resume a ring that was running before the page was refreshed.
			// The proxy can probe the master device directly — no need to wait for
			// browser-side WebSocket connections to establish first.
			await tryResumeRing();
		}
	});

	if (webUsbSupported) {
		initUsbManager();

		// Listen for USB connect/disconnect events
		cleanupUsbConnect = onUsbConnect(handleUsbConnect);
		cleanupUsbDisconnect = onUsbDisconnect(handleUsbDisconnect);

		// Try to reconnect to previously paired USB devices
		const pairedDevices = await getPairedDevices();
		for (const usbDevice of pairedDevices) {
			const existing = managedDevices.find(d => d.usbDevice === usbDevice || d.serial === usbDevice.serialNumber);
			if (existing) continue;

			const managed = createManagedDevice(usbDevice);
			managedDevices = [...managedDevices, managed];
			connectAndProbe(managed.serial).catch(() => {});
		}
	}
}

/**
 * Request user to connect a new USB device (triggers browser permission dialog).
 * Returns true if a device was added, false if user cancelled.
 */
export async function addDevice(): Promise<boolean> {
	const usbDevice = await requestDevice();
	if (!usbDevice) return false; // User cancelled

	// Check if already tracked
	const existing = managedDevices.find(d =>
		d.usbDevice === usbDevice || d.serial === (usbDevice.serialNumber ?? '')
	);
	if (existing) {
		// Reconnect if disconnected
		if (!existing.adb) {
			await connectAndProbe(existing.serial);
		}
		return true;
	}

	const managed = createManagedDevice(usbDevice);
	managedDevices = [...managedDevices, managed];
	await connectAndProbe(managed.serial);
	return true;
}

/**
 * Keep prompting for USB devices until the user cancels.
 * Returns the number of devices added.
 */
export async function addMultipleDevices(): Promise<number> {
	let count = 0;
	while (true) {
		const added = await addDevice();
		if (!added) break; // User cancelled
		count++;
	}
	return count;
}

/**
 * Connect to a TCP/IP ADB device (ethernet phones, network ADB).
 */
export async function addTcpDevice(host: string, port: number = 5555) {
	const serial = `${host}:${port}`;

	// Check if already tracked
	const existing = managedDevices.find(d => d.serial === serial);
	if (existing) {
		if (!existing.adb) {
			await connectAndProbe(existing.serial);
		}
		return;
	}

	const managed = createTcpManagedDevice(host, port);
	managedDevices = [...managedDevices, managed];

	// Save for auto-reconnect
	saveTcpDevice({ host, port });

	await connectAndProbe(managed.serial);
}

/**
 * Connect to multiple TCP/IP ADB devices at once.
 */
export async function addMultipleTcpDevices(entries: Array<{ host: string; port?: number }>) {
	const promises = entries.map(e => addTcpDevice(e.host, e.port ?? 5555));
	await Promise.allSettled(promises);
}

/**
 * Scan a network range for ADB devices. Found devices are auto-added.
 */
export async function scanAndAddDevices(ips: string[], port: number = 5555) {
	scanAbortController = new AbortController();
	scanProgress = { type: 'start', total: ips.length, scanned: 0, found: 0 };

	try {
		const foundIps = await scanNetwork(ips, port, (event) => {
			scanProgress = event;

			// Auto-add found devices immediately
			if (event.type === 'found' && event.ip) {
				const serial = `${event.ip}:${port}`;
				if (!managedDevices.find(d => d.serial === serial)) {
					const managed = createTcpManagedDevice(event.ip, port);
					managedDevices = [...managedDevices, managed];
					saveTcpDevice({ host: event.ip, port });
					connectAndProbe(managed.serial).catch(() => {});
				}
			}
		}, scanAbortController.signal);

		return foundIps;
	} catch (e) {
		if ((e as Error).name !== 'AbortError') {
			globalError = `Scan failed: ${e instanceof Error ? e.message : e}`;
		}
		return [];
	} finally {
		scanAbortController = null;
		// Keep progress visible briefly so user sees final result
		setTimeout(() => { scanProgress = null; }, 5000);
	}
}

/**
 * Cancel an in-progress network scan.
 */
export function cancelScan() {
	scanAbortController?.abort();
}

/**
 * Connect to a device (USB or TCP) and probe its info.
 */
async function connectAndProbe(serial: string) {
	if (ringForming) {
		log(`connectAndProbe(${serial}) BLOCKED — ringForming=true`);
		return;
	}
	const idx = managedDevices.findIndex(d => d.serial === serial);
	if (idx === -1) return;

	log(`connectAndProbe(${serial}) starting`);
	updateDeviceState(serial, 'connecting');

	try {
		const device = managedDevices[idx];
		let adb;

		if (device.transport === 'tcp') {
			if (!device.tcpHost) throw new Error('No TCP host configured');
			log(`connectAndProbe(${serial}) connecting TCP ${device.tcpHost}:${device.tcpPort ?? 5555}`);
			adb = await connectTcpDevice(device.tcpHost, device.tcpPort ?? 5555);
		} else {
			if (!device.usbDevice) throw new Error('No USB device reference');
			log(`connectAndProbe(${serial}) connecting USB`);
			adb = await connectDevice(device.usbDevice);
		}

		log(`connectAndProbe(${serial}) connected, probing...`);
		// Update with ADB connection
		managedDevices = managedDevices.map(d =>
			d.serial === serial ? { ...d, adb, state: 'probing' as DeviceState } : d
		);

		// Probe device info
		const info = await probeDevice(adb);
		const models = await listModels(adb);

		managedDevices = managedDevices.map(d =>
			d.serial === serial
				? { ...d, info, models, state: 'ready' as DeviceState, lastProbeAt: Date.now(), lastError: null }
				: d
		);

		// Keep ringMasterDevice in sync so health polling + chat work after reconnect
		if (ringMasterDevice && serial === ringMasterDevice.serial) {
			ringMasterDevice = { ...ringMasterDevice, adb, info };
			log(`connectAndProbe(${serial}) synced ringMasterDevice — ip=${info.ipAddress}, adb=${!!adb}`);

			// After a page refresh resume, the ring is active but health is 'reconnecting'
			// because we didn't have a live ADB connection yet. Now we do — verify the
			// ring is still alive via the proxy and mark it ready.
			if (ringActive && !ringHealth.ready) {
				log('connectAndProbe: master ADB live, starting health poll to verify ring');
				startHealthPolling();
			}
		}

		log(`connectAndProbe(${serial}) READY — ip=${info.ipAddress}`);
	} catch (e) {
		const errMsg = e instanceof Error ? e.message : String(e);
		logErr(`connectAndProbe(${serial}) FAILED:`, errMsg);
		managedDevices = managedDevices.map(d =>
			d.serial === serial ? { ...d, state: 'error' as DeviceState, lastError: errMsg } : d
		);
	}
}

function updateDeviceState(serial: string, state: DeviceState) {
	managedDevices = managedDevices.map(d =>
		d.serial === serial ? { ...d, state } : d
	);
}

function handleUsbConnect(usbDevice: USBDevice) {
	if (ringForming) { log(`handleUsbConnect BLOCKED — ringForming`); return; }
	const serial = usbDevice.serialNumber;
	if (!serial) return;

	const existing = managedDevices.find(d => d.serial === serial);
	if (existing) {
		// Device reconnected
		managedDevices = managedDevices.map(d =>
			d.serial === serial ? { ...d, usbDevice, state: 'disconnected' as DeviceState } : d
		);
		connectAndProbe(serial).catch(() => {});
	} else {
		const managed = createManagedDevice(usbDevice);
		managedDevices = [...managedDevices, managed];
		connectAndProbe(managed.serial).catch(() => {});
	}
}

function handleUsbDisconnect(usbDevice: USBDevice) {
	if (ringForming) { log(`handleUsbDisconnect BLOCKED — ringForming`); return; }
	const serial = usbDevice.serialNumber;
	if (!serial) return;

	managedDevices = managedDevices.map(d => {
		if (d.serial === serial) {
			// Clean up ADB connection
			if (d.adb) disconnectDevice(d.adb).catch(() => {});
			return { ...d, adb: null, state: 'disconnected' as DeviceState };
		}
		return d;
	});
}

/**
 * Refresh info for all connected devices.
 */
export async function refreshAllDevices() {
	if (ringForming) { log('refreshAllDevices BLOCKED — ringForming'); return; }
	isRefreshing = true;
	try {
		await Promise.all(
			managedDevices
				.filter(d => d.adb)
				.map(async (d) => {
					try {
						const partial = await quickProbe(d.adb!);
						managedDevices = managedDevices.map(md =>
							md.serial === d.serial && md.info
								? { ...md, info: { ...md.info, ...partial }, lastProbeAt: Date.now() }
								: md
						);
					} catch { /* ignore */ }
				})
		);
	} finally {
		isRefreshing = false;
	}
}

/**
 * Remove a device from the tracked list.
 */
export async function removeDevice(serial: string) {
	const device = managedDevices.find(d => d.serial === serial);
	if (device?.adb) {
		await disconnectDevice(device.adb).catch(() => {});
	}
	// Remove TCP device from localStorage auto-reconnect list
	if (device?.transport === 'tcp' && device.tcpHost) {
		removeTcpDevice(device.tcpHost, device.tcpPort ?? 5555);
	}
	managedDevices = managedDevices.filter(d => d.serial !== serial);
	selectedSerials = new Set([...selectedSerials].filter(s => s !== serial));
}

/**
 * Reconnect a disconnected/errored device.
 */
export async function reconnectDevice(serial: string) {
	await connectAndProbe(serial);
}

// ─── Polling ─────────────────────────────────────────────────────────

export function startPolling() {
	if (!browser || pollInterval) return;
	pollInterval = setInterval(refreshAllDevices, POLL_MS);
}

export function stopPolling() {
	if (pollInterval) { clearInterval(pollInterval); pollInterval = null; }
	if (healthInterval) { clearInterval(healthInterval); healthInterval = null; }
}

function startHealthPolling() {
	if (healthInterval) { log('startHealthPolling: already running'); return; }
	log('startHealthPolling: starting', { masterSerial: ringMasterDevice?.serial, masterIp: ringMasterDevice?.info?.ipAddress });
	pollRingHealth();
	healthInterval = setInterval(pollRingHealth, HEALTH_POLL_MS);
}

function stopHealthPolling() {
	if (healthInterval) { clearInterval(healthInterval); healthInterval = null; }
}

async function pollRingHealth() {
	if (!ringMasterDevice) {
		log('pollRingHealth: no ringMasterDevice, stopping');
		stopHealthPolling();
		return;
	}

	const masterIp = ringMasterDevice.info?.ipAddress?.trim();
	if (!masterIp) {
		logWarn('pollRingHealth: ringMasterDevice has no ipAddress!', {
			serial: ringMasterDevice.serial,
			hasInfo: !!ringMasterDevice.info,
			info: ringMasterDevice.info,
		});
		return;
	}

	const cmd = `curl -s http://${masterIp}:${ringHttpPort}/api/ring 2>/dev/null`;
	log(`pollRingHealth: proxy shell → ${ringMasterDevice.serial}: ${cmd}`);

	try {
		const resp = await fetch(`http://${location.hostname}:3002/shell`, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ serial: ringMasterDevice.serial, cmd }),
		});
		const result = await resp.json();
		log('pollRingHealth: proxy response', { ok: result.ok, stdout: result.stdout?.slice(0, 200), stderr: result.stderr?.slice(0, 200) });

		if (result.ok && result.stdout?.trim()) {
			const data = JSON.parse(result.stdout.trim());
			const ready = (data.n_world > 0 || data.world_size > 0);
			log(`pollRingHealth: n_world=${data.n_world}, world_size=${data.world_size}, ready=${ready}`);
			ringHealth = { ready, status: ready ? 'ready' : 'loading' };
			if (ready) {
				log('pollRingHealth: ring READY, stopping health poll');
				stopHealthPolling();
				// Persist ring state so we can resume after browser refresh
				saveRingState();
				// Fetch the active chat template from the running master
				fetchChatTemplate().catch(() => {});
			}
		} else {
			logWarn('pollRingHealth: empty/failed response from proxy', result);
		}
	} catch (e) {
		logErr('pollRingHealth: fetch error:', e);
		ringHealth = { ready: false, status: 'loading' };
	}
}

// ─── Ring ────────────────────────────────────────────────────────────

export async function handleStartRing(config: RingFormationConfig) {
	log('handleStartRing: BEGIN', { deviceCount: config.devices.length, masterSerial: config.devices[0]?.serial });

	// CRITICAL: Block ALL background ADB access during ring formation.
	ringForming = true;
	stopPolling();
	log('handleStartRing: ringForming=true, polling stopped');

	// Disconnect ALL ya-webadb WebSocket connections.
	// The ring orchestrator uses HTTP→proxy→adb (path #2), not WebUSB (path #1).
	const connectedCount = managedDevices.filter(d => d.adb).length;
	log(`handleStartRing: disconnecting ${connectedCount} ya-webadb connections`);
	for (const d of managedDevices) {
		if (d.adb) {
			try { await disconnectDevice(d.adb); } catch { /* ignore */ }
		}
	}
	managedDevices = managedDevices.map(d => ({
		...d,
		adb: null,
		state: 'disconnected' as DeviceState,
	}));
	log('handleStartRing: all WS connections closed, devices set to disconnected');

	try {
		ringActive = true;
		ringHealth = { ready: false, status: 'loading' };
		// Save the master device info (serial + device info for health polling).
		// The ADB connection is null now — health polling uses the proxy, not ya-webadb.
		ringMasterDevice = { ...config.devices[0], adb: null };
		ringHttpPort = config.httpPort;
		ringFormationProgress = null;
		log('handleStartRing: calling orchestrateRing...');

		await orchestrateRing(config, (progress) => {
			log('handleStartRing: progress', progress.phase, progress.message);
			ringFormationProgress = progress;
		});

		log('handleStartRing: orchestrateRing completed, starting health polling');
		startHealthPolling();
	} catch (e) {
		logErr('handleStartRing: FAILED:', e);
		ringActive = false;
		ringHealth = { ready: false, status: 'inactive' };
		ringFormationProgress = null;
		clearRingState();
		globalError = `Ring start failed: ${e instanceof Error ? e.message : e}`;
		startPolling();
		throw e;
	} finally {
		ringForming = false;
		log('handleStartRing: ringForming=false, reconnecting devices...');
		// Reconnect all devices now that ring formation is done.
		const serials = managedDevices.map(d => d.serial);
		for (const serial of serials) {
			connectAndProbe(serial).catch(() => {});
		}
	}
}

export async function handleStopRing() {
	log('handleStopRing: BEGIN');
	try {
		await orchestrateStopRing(managedDevices);
		log('handleStopRing: orchestrateStopRing completed');
	} catch (e) { logErr('handleStopRing error:', e); } finally {
		ringActive = false;
		ringHealth = { ready: false, status: 'inactive' };
		ringMasterDevice = null;
		ringFormationProgress = null;
		ringChatTemplate = null;
		clearRingState();
		stopHealthPolling();
		// Resume device polling after ring is stopped
		startPolling();
	}
}

// ─── Chat Template (runtime switching) ───────────────────────────────

/** Current chat template on the running ring (null = unknown / ring not running) */
let ringChatTemplate = $state<string | null>(null);

export function getRingChatTemplate() { return ringChatTemplate; }

/** Fetch current chat template from the running master */
export async function fetchChatTemplate(): Promise<{ current: string; available: string[] } | null> {
	if (!ringMasterDevice?.adb || !ringActive) return null;
	const port = ringHttpPort ?? 8080;
	try {
		const resp = await adbFetch(ringMasterDevice.adb, '/api/chat-template', { port });
		if (!resp.ok) return null;
		const data = resp.json();
		ringChatTemplate = data.current || '';
		return data;
	} catch (e) {
		logErr('fetchChatTemplate error:', e);
		return null;
	}
}

/** Hot-swap chat template on the running master */
export async function setChatTemplate(template: string): Promise<boolean> {
	if (!ringMasterDevice?.adb || !ringActive) return false;
	const port = ringHttpPort ?? 8080;
	try {
		const resp = await adbFetch(ringMasterDevice.adb, '/api/chat-template', {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ chat_template: template }),
			port,
		});
		if (!resp.ok) {
			logErr('setChatTemplate failed:', resp.text);
			return false;
		}
		const data = resp.json();
		ringChatTemplate = data.chat_template || template;
		log(`setChatTemplate: switched to "${ringChatTemplate}"`);
		return true;
	} catch (e) {
		logErr('setChatTemplate error:', e);
		return false;
	}
}

// ─── Models ──────────────────────────────────────────────────────────

export async function refreshModels() {
	if (ringForming) { log('refreshModels BLOCKED — ringForming'); return; }
	const connected = managedDevices.filter(d => d.adb);
	for (const d of connected) {
		try {
			const models = await scanModels(d.adb!);
			deviceModels = new Map(deviceModels);
			deviceModels.set(d.serial, models);
			// Also update models list on the device
			managedDevices = managedDevices.map(md =>
				md.serial === d.serial ? { ...md, models: models.map(m => m.name) } : md
			);
		} catch { /* ignore */ }
	}
}

// ─── Chat ────────────────────────────────────────────────────────────

function generateId(): string {
	return Math.random().toString(36).slice(2, 10);
}

function loadConversations() {
	if (!browser) return;
	try {
		const raw = localStorage.getItem(STORAGE_KEY);
		if (raw) conversations = JSON.parse(raw);
	} catch { /* ignore */ }
}

function saveConversations() {
	if (!browser) return;
	localStorage.setItem(STORAGE_KEY, JSON.stringify(conversations));
}

export function newConversation(): string {
	const id = generateId();
	const conv: Conversation = {
		id,
		name: `Chat ${conversations.length + 1}`,
		messages: [],
		createdAt: Date.now(),
		updatedAt: Date.now(),
	};
	conversations = [conv, ...conversations];
	activeConversationId = id;
	saveConversations();
	return id;
}

export function setActiveConversation(id: string) {
	activeConversationId = id;
}

export function deleteConversation(id: string) {
	conversations = conversations.filter((c) => c.id !== id);
	if (activeConversationId === id) {
		activeConversationId = conversations[0]?.id ?? null;
	}
	saveConversations();
}

export function stopStreaming() {
	abortController?.abort();
}

export async function sendMessage(content: string) {
	if (ringForming) {
		globalError = 'Ring is being formed. Please wait.';
		return;
	}
	if (!ringMasterDevice?.adb) {
		globalError = ringActive ? 'Reconnecting to master device — please wait a moment and try again.' : 'No ring master connected. Start a ring first.';
		return;
	}

	if (!activeConversationId) newConversation();
	const conv = conversations.find((c) => c.id === activeConversationId);
	if (!conv) return;

	// Auto-name from first message
	if (conv.messages.length === 0) {
		conv.name = content.slice(0, 40) + (content.length > 40 ? '...' : '');
	}

	const userMsg: ChatMessage = { id: generateId(), role: 'user', content, timestamp: Date.now() };
	const assistantMsgId = generateId();
	const assistantMsg: ChatMessage = { id: assistantMsgId, role: 'assistant', content: '', timestamp: Date.now() };

	conv.messages = [...conv.messages, userMsg, assistantMsg];
	conv.updatedAt = Date.now();
	conversations = [...conversations];
	saveConversations();

	streaming = true;
	streamingContent = '';
	streamingTokenCount = 0;
	streamingTtftMs = 0;
	streamingTps = 0;
	streamingStartTime = Date.now();
	const startTime = Date.now();
	let tokenCount = 0;
	let firstTokenTime = 0;

	abortController = new AbortController();

	const port = ringHttpPort ?? 8080;

	try {
		const body = JSON.stringify({
			messages: conv.messages
				.filter((m) => m.role !== 'system' && m.id !== assistantMsgId)
				.map((m) => ({ role: m.role, content: m.content })),
			stream: true,
			temperature: settings.temperature,
			top_k: settings.topK,
			top_p: settings.topP,
			min_p: settings.minP,
			max_tokens: settings.maxTokens,
			repeat_penalty: settings.repeatPenalty,
			repeat_last_n: settings.repeatLastN,
			frequency_penalty: settings.frequencyPenalty,
			presence_penalty: settings.presencePenalty,
			...(settings.seed >= 0 ? { seed: settings.seed } : {}),
			stop: ['<|im_end|>', '<|end|>', '</s>'],
		});

		const sseStream = adbFetchSSE(ringMasterDevice.adb!, '/v1/chat/completions', {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body,
			port,
			signal: abortController.signal,
		});

		for await (const data of sseStream) {
			try {
				const parsed = JSON.parse(data);
				const delta = parsed.choices?.[0]?.delta?.content;
				if (delta) {
					// Strip raw special tokens that may leak from the model
					const clean = delta
						.replace(/<\|im_end\|>/g, '')
						.replace(/<\|im_start\|>/g, '')
						.replace(/<\|end\|>/g, '')
						.replace(/<\/s>/g, '');
					if (clean) {
						if (tokenCount === 0) {
							firstTokenTime = Date.now() - startTime;
							streamingTtftMs = firstTokenTime;
						}
						tokenCount++;
						streamingTokenCount = tokenCount;
						streamingContent += clean;
						// Update live tok/s
						const elapsed = (Date.now() - startTime) / 1000;
						streamingTps = elapsed > 0 ? tokenCount / elapsed : 0;
					}
				}
			} catch { /* skip malformed SSE */ }
		}
	} catch (e) {
		if ((e as Error).name !== 'AbortError') {
			streamingContent += `\n\n[Error: ${e}]`;
		}
	} finally {
		// Write final content into conversation
		const c = conversations.find((c) => c.id === activeConversationId);
		const msg = c?.messages.find((m) => m.id === assistantMsgId);
		if (msg) {
			// Clean any trailing special tokens from final content
			if (streamingContent) {
				msg.content = streamingContent.replace(/<\|im_end\|>/g, '').replace(/<\|im_start\|>[\s\S]*/g, '').trim();
			}
			if (tokenCount > 0) {
				msg.ttftMs = firstTokenTime;
				const elapsed = (Date.now() - startTime) / 1000;
				msg.tps = elapsed > 0 ? tokenCount / elapsed : 0;
			}
		}
		if (c) c.updatedAt = Date.now();
		streaming = false;
		streamingContent = '';
		streamingTokenCount = 0;
		streamingStartTime = 0;
		streamingTtftMs = 0;
		streamingTps = 0;
		abortController = null;
		conversations = [...conversations];
		saveConversations();

		// Fetch speculative decoding stats from /api/spec (non-blocking)
		if (ringMasterDevice?.adb && tokenCount > 0) {
			fetchSpecStats(ringMasterDevice.adb, port, assistantMsgId).catch(() => {});
		}
	}
}

/** Fetch speculative decoding stats from the master device's /api/spec endpoint */
async function fetchSpecStats(adb: import('@yume-chan/adb').Adb, port: number, msgId: string) {
	try {
		const resp = await adbFetch(adb, '/api/spec', { port });
		if (!resp.ok) return;
		const data = resp.json();
		const stats: SpecStats = {
			draftedTotal: data.n_drafted_total ?? 0,
			acceptedTotal: data.n_accepted_total ?? 0,
			acceptRatePct: data.accept_rate_pct ?? 0,
			tokPerS: data.tok_per_s ?? 0,
			specCycles: data.n_spec_cycles ?? 0,
			tokensPredictedTotal: data.n_tokens_predicted_total ?? 0,
		};
		// Update the message with spec stats
		const conv = conversations.find((c) => c.id === activeConversationId);
		const msg = conv?.messages.find((m) => m.id === msgId);
		if (msg) {
			msg.specStats = stats;
			// Use server-side tok/s if available (more accurate)
			if (stats.tokPerS > 0) msg.tps = stats.tokPerS;
			conversations = [...conversations];
			saveConversations();
		}
	} catch {
		// Spec stats are best-effort, don't fail the chat
	}
}

// ─── Cleanup ─────────────────────────────────────────────────────────

export function destroy() {
	stopPolling();
	cleanupUsbConnect?.();
	cleanupUsbDisconnect?.();
	cleanupUsbConnect = null;
	cleanupUsbDisconnect = null;

	// Disconnect all devices
	for (const d of managedDevices) {
		if (d.adb) disconnectDevice(d.adb).catch(() => {});
	}
}

// ─── Initialize ──────────────────────────────────────────────────────

if (browser) {
	loadConversations();
	loadGroups();
	loadSettings();
	// Push saved ADB path to proxy on startup (proxy validates before accepting)
	if (settings.adbPath) {
		configureProxy(settings.adbPath).catch(() => {});
	}
}
