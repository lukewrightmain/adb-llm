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
	onUsbConnect,
	onUsbDisconnect,
} from '$lib/services/adb-manager';
import { probeDevice, quickProbe } from '$lib/services/device-prober';
import { adbFetch, adbFetchSSE } from '$lib/services/adb-http';
import { startRing as orchestrateRing, stopRing as orchestrateStopRing, killAllProcesses } from '$lib/services/ring-orchestrator';
import { scanModels, type ModelOnDevice } from '$lib/services/model-manager';
import { listModels } from '$lib/services/adb-shell';

// ─── State ───────────────────────────────────────────────────────────

let managedDevices = $state<ManagedDevice[]>([]);
let webUsbSupported = $state(false);
let ringActive = $state(false);
let ringHealth = $state<RingHealth>({ ready: false, status: 'inactive' });
let ringFormationProgress = $state<RingFormationProgress | null>(null);
let ringMasterDevice = $state<ManagedDevice | null>(null);

// Chat
let conversations = $state<Conversation[]>([]);
let activeConversationId = $state<string | null>(null);
let streaming = $state(false);
let streamingContent = $state('');
let abortController = $state<AbortController | null>(null);

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

// Settings (persisted in localStorage)
const DEFAULT_SETTINGS: RingSettings = {
	speculative: true,
	draftMax: 24,
	totalLayers: 62,
	contextSize: 2048,
	prefetch: true,
	defaultModel: '',
	defaultDraftModel: '',
};
let settings = $state<RingSettings>({ ...DEFAULT_SETTINGS });

// Polling
let pollInterval: ReturnType<typeof setInterval> | null = null;
let healthInterval: ReturnType<typeof setInterval> | null = null;

const STORAGE_KEY = 'cellswarm-conversations';
const GROUPS_STORAGE_KEY = 'cellswarm-device-groups';
const SETTINGS_STORAGE_KEY = 'cellswarm-settings';
const POLL_MS = 3000;
const HEALTH_POLL_MS = 3000;

// Event listener cleanup
let cleanupUsbConnect: (() => void) | null = null;
let cleanupUsbDisconnect: (() => void) | null = null;

// ─── Getters ─────────────────────────────────────────────────────────

export function getManagedDevices() { return managedDevices; }
export function getWebUsbSupported() { return webUsbSupported; }
export function isRingActive() { return ringActive; }
export function getRingHealth() { return ringHealth; }
export function getRingFormationProgress() { return ringFormationProgress; }
export function getRingMasterDevice() { return ringMasterDevice; }
export function getConversations() { return conversations; }
export function getActiveConversationId() { return activeConversationId; }
export function isStreaming() { return streaming; }
export function getStreamingContent() { return streamingContent; }
export function getActiveTab() { return activeTab; }
export function getGlobalError() { return globalError; }
export function getIsRefreshing() { return isRefreshing; }
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
 * Initialize WebUSB and try to reconnect to previously paired devices.
 */
export async function initWebUsb() {
	webUsbSupported = isWebUsbSupported();
	if (!webUsbSupported) return;

	initUsbManager();

	// Listen for USB connect/disconnect events
	cleanupUsbConnect = onUsbConnect(handleUsbConnect);
	cleanupUsbDisconnect = onUsbDisconnect(handleUsbDisconnect);

	// Try to reconnect to previously paired devices
	const pairedDevices = await getPairedDevices();
	for (const usbDevice of pairedDevices) {
		const existing = managedDevices.find(d => d.usbDevice === usbDevice || d.serial === usbDevice.serialNumber);
		if (existing) continue;

		const managed = createManagedDevice(usbDevice);
		managedDevices = [...managedDevices, managed];
		// Auto-connect in background
		connectAndProbe(managed.serial).catch(() => {});
	}
}

/**
 * Request user to connect a new USB device (triggers browser permission dialog).
 */
export async function addDevice() {
	const usbDevice = await requestDevice();
	if (!usbDevice) return; // User cancelled

	// Check if already tracked
	const existing = managedDevices.find(d =>
		d.usbDevice === usbDevice || d.serial === (usbDevice.serialNumber ?? '')
	);
	if (existing) {
		// Reconnect if disconnected
		if (!existing.adb) {
			await connectAndProbe(existing.serial);
		}
		return;
	}

	const managed = createManagedDevice(usbDevice);
	managedDevices = [...managedDevices, managed];
	await connectAndProbe(managed.serial);
}

/**
 * Connect to a device and probe its info.
 */
async function connectAndProbe(serial: string) {
	const idx = managedDevices.findIndex(d => d.serial === serial);
	if (idx === -1) return;

	updateDeviceState(serial, 'connecting');

	try {
		const device = managedDevices[idx];
		if (!device.usbDevice) throw new Error('No USB device reference');

		const adb = await connectDevice(device.usbDevice);

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
	} catch (e) {
		const errMsg = e instanceof Error ? e.message : String(e);
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
	managedDevices = managedDevices.filter(d => d.serial !== serial);
	selectedSerials = new Set([...selectedSerials].filter(s => s !== serial));
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
	if (healthInterval) return;
	pollRingHealth();
	healthInterval = setInterval(pollRingHealth, HEALTH_POLL_MS);
}

function stopHealthPolling() {
	if (healthInterval) { clearInterval(healthInterval); healthInterval = null; }
}

async function pollRingHealth() {
	if (!ringMasterDevice?.adb) { stopHealthPolling(); return; }

	try {
		const resp = await adbFetch(ringMasterDevice.adb, '/api/ring', { port: 8080 });
		if (resp.ok) {
			const data = resp.json();
			const ready = (data.n_world > 0 || data.world_size > 0);
			ringHealth = { ready, status: ready ? 'ready' : 'loading' };
			if (ready) stopHealthPolling();
		}
	} catch {
		ringHealth = { ready: false, status: 'loading' };
	}
}

// ─── Ring ────────────────────────────────────────────────────────────

export async function handleStartRing(config: RingFormationConfig) {
	try {
		ringActive = true;
		ringHealth = { ready: false, status: 'loading' };
		ringMasterDevice = config.devices[0];
		ringFormationProgress = null;

		await orchestrateRing(config, (progress) => {
			ringFormationProgress = progress;
		});

		startHealthPolling();
	} catch (e) {
		ringActive = false;
		ringHealth = { ready: false, status: 'inactive' };
		ringFormationProgress = null;
		globalError = `Ring start failed: ${e instanceof Error ? e.message : e}`;
		throw e;
	}
}

export async function handleStopRing() {
	try {
		await orchestrateStopRing(managedDevices);
	} catch { /* ignore */ } finally {
		ringActive = false;
		ringHealth = { ready: false, status: 'inactive' };
		ringMasterDevice = null;
		ringFormationProgress = null;
		stopHealthPolling();
	}
}

// ─── Models ──────────────────────────────────────────────────────────

export async function refreshModels() {
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
	if (!ringMasterDevice?.adb) {
		globalError = 'No ring master connected. Start a ring first.';
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
	const startTime = Date.now();
	let tokenCount = 0;
	let firstTokenTime = 0;

	abortController = new AbortController();

	try {
		const body = JSON.stringify({
			messages: conv.messages
				.filter((m) => m.role !== 'system' && m.id !== assistantMsgId)
				.map((m) => ({ role: m.role, content: m.content })),
			stream: true,
			temperature: 0.7,
			max_tokens: 2048,
		});

		const sseStream = adbFetchSSE(ringMasterDevice.adb!, '/v1/chat/completions', {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body,
			port: 8080,
			signal: abortController.signal,
		});

		for await (const data of sseStream) {
			try {
				const parsed = JSON.parse(data);
				const delta = parsed.choices?.[0]?.delta?.content;
				if (delta) {
					if (tokenCount === 0) firstTokenTime = Date.now() - startTime;
					tokenCount++;
					streamingContent += delta;
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
			if (streamingContent) msg.content = streamingContent;
			if (tokenCount > 0) {
				msg.ttftMs = firstTokenTime;
				const elapsed = (Date.now() - startTime) / 1000;
				msg.tps = elapsed > 0 ? tokenCount / elapsed : 0;
			}
		}
		if (c) c.updatedAt = Date.now();
		streaming = false;
		streamingContent = '';
		abortController = null;
		conversations = [...conversations];
		saveConversations();
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
}
