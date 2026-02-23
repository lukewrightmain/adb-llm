/**
 * AppStore — Central state for the CellSwarm dashboard v2.
 *
 * All polling intervals are tracked and cleaned up in stopPolling().
 * Health polling only runs when ring is active but not yet ready.
 * Job polling only runs when active jobs exist.
 */

import { browser } from '$app/environment';
import type {
	Device,
	DevicesResponse,
	RingStatus,
	RingNode,
	ChatMessage,
	Conversation,
	ModelsResponse,
	JobProgress,
	DeviceGroup,
	TabId,
	RingHealth,
	RingStartConfig,
	RingSettings,
} from '$lib/types';

// ─── State ───────────────────────────────────────────────────────────

let devices = $state<Device[]>([]);
let readyCount = $state(0);
let ringStatus = $state<RingStatus>({ active: false, world_size: 0, model: null, draft_model: null, nodes: [] });
let ringHealth = $state<RingHealth>({ ready: false, status: 'inactive' });
let modelsData = $state<ModelsResponse>({ local_models: [], device_models: {} });
let downloadJobs = $state<JobProgress[]>([]);
let distributeJobs = $state<JobProgress[]>([]);

// Chat
let conversations = $state<Conversation[]>([]);
let activeConversationId = $state<string | null>(null);
let streaming = $state(false);
let streamingContent = $state('');

// Device selection & groups
let selectedSerials = $state<Set<string>>(new Set());
let deviceGroups = $state<DeviceGroup[]>([]);
let activeGroupId = $state<string | null>(null);

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

// Polling handles
let pollInterval: ReturnType<typeof setInterval> | null = null;
let healthInterval: ReturnType<typeof setInterval> | null = null;
let jobInterval: ReturnType<typeof setInterval> | null = null;

const STORAGE_KEY = 'cellswarm-conversations';
const GROUPS_STORAGE_KEY = 'cellswarm-device-groups';
const SETTINGS_STORAGE_KEY = 'cellswarm-settings';
const POLL_MS = 2000;
const HEALTH_POLL_MS = 3000;
const JOB_POLL_MS = 2000;

// ─── Getters ─────────────────────────────────────────────────────────

export function getDevices() { return devices; }
export function getReadyCount() { return readyCount; }
export function getRingStatus() { return ringStatus; }
export function getRingHealth() { return ringHealth; }
export function getModelsData() { return modelsData; }
export function getDownloadJobs() { return downloadJobs; }
export function getDistributeJobs() { return distributeJobs; }
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

export function getActiveConversation(): Conversation | undefined {
	return conversations.find((c) => c.id === activeConversationId);
}

export function getTargetDevicesString(): string {
	if (selectedSerials.size === 0) return 'all';
	return Array.from(selectedSerials).join(',');
}

// ─── Tab ─────────────────────────────────────────────────────────────

export function setActiveTab(tab: TabId) {
	activeTab = tab;
	if (tab === 'models') fetchModels();
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
	selectedSerials = new Set(devices.map((d) => d.serial));
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

export function updateGroupSerials(id: string) {
	const group = deviceGroups.find((g) => g.id === id);
	if (group) {
		group.serials = Array.from(selectedSerials);
		deviceGroups = [...deviceGroups];
		saveGroups();
	}
}

// ─── Polling ─────────────────────────────────────────────────────────

export function startPolling() {
	if (!browser || pollInterval) return;
	poll();
	pollInterval = setInterval(poll, POLL_MS);
}

export function stopPolling() {
	if (pollInterval) { clearInterval(pollInterval); pollInterval = null; }
	if (healthInterval) { clearInterval(healthInterval); healthInterval = null; }
	if (jobInterval) { clearInterval(jobInterval); jobInterval = null; }
}

async function poll() {
	try {
		const [devResp, ringResp] = await Promise.all([
			fetch('/api/devices').then((r) => r.json()) as Promise<DevicesResponse>,
			fetch('/api/ring/status').then((r) => r.json()) as Promise<RingStatus>,
		]);
		devices = devResp.devices;
		readyCount = devResp.ready_count;
		ringStatus = ringResp;

		// Update ring health state
		if (!ringResp.active) {
			ringHealth = { ready: false, status: 'inactive' };
			stopHealthPolling();
		} else if (!ringHealth.ready) {
			// Ring is active but not yet confirmed ready — keep polling health
			if (ringHealth.status === 'inactive') {
				ringHealth = { ready: false, status: 'loading' };
			}
			startHealthPolling();
		}
	} catch (e) {
		console.warn('Poll failed:', e);
	}
}

function startHealthPolling() {
	if (healthInterval) return;
	pollHealth();
	healthInterval = setInterval(pollHealth, HEALTH_POLL_MS);
}

function stopHealthPolling() {
	if (healthInterval) { clearInterval(healthInterval); healthInterval = null; }
}

async function pollHealth() {
	if (!ringStatus.active) { stopHealthPolling(); return; }
	try {
		// Try /api/ring/health first (newer servers), fall back to /v1/health (llama-server proxy)
		let resp = await fetch('/api/ring/health');
		if (resp.status === 404) {
			resp = await fetch('/v1/health');
		}
		// Parse JSON regardless of status code — 503 returns {"error":{"message":"Loading model"}}
		const data = await resp.json().catch(() => null);
		if (!data) {
			ringHealth = { ready: false, status: 'loading' };
			return;
		}

		// /api/ring/health: {ready: true, status: "ready"}
		// /v1/health OK: {status: "ok"}
		// /v1/health 503: {error: {code: 503, message: "Loading model"}}
		const isReady = data.ready === true || data.status === 'ready' || data.status === 'ok';
		ringHealth = { ready: isReady, status: isReady ? 'ready' : 'loading' };
		if (isReady) stopHealthPolling();
	} catch {
		ringHealth = { ready: false, status: 'loading' };
	}
}


export function startJobPolling() {
	if (jobInterval) return;
	pollJobProgress();
	jobInterval = setInterval(pollJobProgress, JOB_POLL_MS);
}

function stopJobPolling() {
	if (jobInterval) { clearInterval(jobInterval); jobInterval = null; }
}

// ─── API calls ───────────────────────────────────────────────────────

export async function refreshDevices() {
	isRefreshing = true;
	try {
		const resp: DevicesResponse = await fetch('/api/devices/refresh', { method: 'POST' }).then((r) => r.json());
		devices = resp.devices;
		readyCount = resp.ready_count;
	} catch (e) {
		globalError = `Refresh failed: ${e}`;
	} finally {
		isRefreshing = false;
	}
}

export async function fetchModels() {
	try {
		modelsData = await fetch('/api/models').then((r) => r.json());
	} catch (e) {
		globalError = `Models fetch failed: ${e}`;
	}
}

export async function startRing(config: RingStartConfig) {
	try {
		const resp = await fetch('/api/ring/start', {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify(config),
		});
		if (!resp.ok) {
			const text = await resp.text();
			throw new Error(text);
		}
		// The backend launches the ring in a background task and returns immediately.
		// Poll /api/ring/launch-status for progress (done by RingPanel's progressTimer),
		// and poll /api/ring/health for readiness once the ring reports active.
		ringHealth = { ready: false, status: 'loading' };
		startHealthPolling();
	} catch (e) {
		globalError = `Start ring failed: ${e}`;
		throw e;
	}
}

export async function stopRing() {
	try {
		const resp = await fetch('/api/ring/stop', { method: 'POST' });
		if (!resp.ok) throw new Error(await resp.text());
		ringHealth = { ready: false, status: 'inactive' };
		stopHealthPolling();
		await poll();
	} catch (e) {
		globalError = `Stop ring failed: ${e}`;
		throw e;
	}
}

export async function downloadModel(params: { repo_id?: string; filename?: string; url?: string }) {
	const resp = await fetch('/api/models/download', {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify(params),
	});
	if (!resp.ok) throw new Error(await resp.text());
	startJobPolling();
	return resp.json();
}

export async function distributeModel(model_name: string, target_devices = 'all') {
	const resp = await fetch('/api/models/distribute', {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify({ model_name, devices: target_devices }),
	});
	if (!resp.ok) throw new Error(await resp.text());
	startJobPolling();
	return resp.json();
}

export async function pollJobProgress() {
	try {
		const [dl, dist] = await Promise.all([
			fetch('/api/models/downloads').then((r) => r.json()) as Promise<JobProgress[]>,
			fetch('/api/models/distributions').then((r) => r.json()) as Promise<JobProgress[]>,
		]);
		downloadJobs = dl;
		distributeJobs = dist;

		// Auto-stop if no active jobs
		const hasActive = [...dl, ...dist].some((j) => j.status === 'running' || j.status === 'pending');
		if (!hasActive) stopJobPolling();
	} catch {
		// silent
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

export async function sendMessage(content: string) {
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
	// Reassign conversations to trigger top-level reactivity
	conversations = [...conversations];
	saveConversations();

	streaming = true;
	const startTime = Date.now();
	let tokenCount = 0;
	let firstTokenTime = 0;

	// Use a separate $state for streaming content so reactivity works without
	// having to spread the entire conversations array on every token.
	// We accumulate content here and the ChatPanel reads it directly.

	try {
		const resp = await fetch('/v1/chat/completions', {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({
				messages: conv.messages
					.filter((m) => m.role !== 'system' && m.id !== assistantMsgId)
					.map((m) => ({ role: m.role, content: m.content })),
				stream: true,
				temperature: 0.7,
				max_tokens: 2048,
			}),
		});

		if (!resp.ok || !resp.body) {
			// Try to get a useful error message from the response
			let errMsg = resp.statusText;
			try {
				const errBody = await resp.json();
				errMsg = errBody?.error?.message || errBody?.detail || errBody?.message || resp.statusText;
			} catch { /* use statusText */ }
			streamingContent = `Error: ${errMsg}`;
			// Write error into conversation and finish
			const c = conversations.find((c) => c.id === activeConversationId);
			const msg = c?.messages.find((m) => m.id === assistantMsgId);
			if (msg) msg.content = streamingContent;
			conversations = [...conversations];
			saveConversations();
			streaming = false;
			streamingContent = '';
			return;
		}

		const reader = resp.body.getReader();
		const decoder = new TextDecoder();
		let buffer = '';

		while (true) {
			const { done, value } = await reader.read();
			if (done) break;

			buffer += decoder.decode(value, { stream: true });
			const lines = buffer.split('\n');
			buffer = lines.pop() || '';

			for (const line of lines) {
				if (!line.startsWith('data: ')) continue;
				const data = line.slice(6).trim();
				if (data === '[DONE]') continue;

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
		}
	} catch (e) {
		streamingContent += `\n\n[Error: ${e}]`;
	} finally {
		// Write final content into conversation
		const c = conversations.find((c) => c.id === activeConversationId);
		const msg = c?.messages.find((m) => m.id === assistantMsgId);
		if (msg) {
			// Only overwrite content from streamingContent if we got any
			if (streamingContent) msg.content = streamingContent;
			// Only write metrics if we got real tokens (avoid 0ms / 0 tok/s)
			if (tokenCount > 0) {
				msg.ttftMs = firstTokenTime;
				const elapsed = (Date.now() - startTime) / 1000;
				msg.tps = elapsed > 0 ? tokenCount / elapsed : 0;
			}
		}
		if (c) c.updatedAt = Date.now();
		streaming = false;
		streamingContent = '';
		conversations = [...conversations];
		saveConversations();
	}
}

// ─── Initialize ──────────────────────────────────────────────────────

if (browser) {
	loadConversations();
	loadGroups();
	loadSettings();
}
