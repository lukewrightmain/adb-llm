/**
 * AppStore — Central state for the CellSwarm dashboard.
 *
 * Manages: devices, ring topology, chat, models.
 * Polls /api/devices and /api/ring/status every 2s.
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
} from '$lib/types';

// ─── State ───────────────────────────────────────────────────────────

let devices = $state<Device[]>([]);
let readyCount = $state(0);
let ringStatus = $state<RingStatus>({ active: false, world_size: 0, model: null, nodes: [] });
let modelsData = $state<ModelsResponse>({ local_models: [], device_models: {} });
let downloadJobs = $state<JobProgress[]>([]);
let distributeJobs = $state<JobProgress[]>([]);

// Chat
let conversations = $state<Conversation[]>([]);
let activeConversationId = $state<string | null>(null);
let chatStarted = $state(false);
let topologyMinimized = $state(false);
let streaming = $state(false);

// Device selection & groups
let selectedSerials = $state<Set<string>>(new Set());
let deviceGroups = $state<DeviceGroup[]>([]);
let activeGroupId = $state<string | null>(null);

// Tab state
let activeTab = $state<'topology' | 'chat' | 'models'>('topology');

// Polling
let pollInterval: ReturnType<typeof setInterval> | null = null;
let lastPollTime = $state(0);

const STORAGE_KEY = 'cellswarm-conversations';
const GROUPS_STORAGE_KEY = 'cellswarm-device-groups';
const POLL_MS = 2000;

// ─── Getters (exported as functions for $derived) ────────────────────

export function getDevices() { return devices; }
export function getReadyCount() { return readyCount; }
export function getRingStatus() { return ringStatus; }
export function getModelsData() { return modelsData; }
export function getDownloadJobs() { return downloadJobs; }
export function getDistributeJobs() { return distributeJobs; }
export function getConversations() { return conversations; }
export function getActiveConversationId() { return activeConversationId; }
export function hasStartedChat() { return chatStarted; }
export function isTopologyMinimized() { return topologyMinimized; }
export function isStreaming() { return streaming; }
export function getActiveTab() { return activeTab; }
export function getLastPollTime() { return lastPollTime; }

export function getActiveConversation(): Conversation | undefined {
	return conversations.find((c) => c.id === activeConversationId);
}

export function getSelectedSerials() { return selectedSerials; }
export function getDeviceGroups() { return deviceGroups; }
export function getActiveGroupId() { return activeGroupId; }

export function getTargetDevicesString(): string {
	if (selectedSerials.size === 0) return 'all';
	return Array.from(selectedSerials).join(',');
}

// ─── Actions ─────────────────────────────────────────────────────────

export function setActiveTab(tab: 'topology' | 'chat' | 'models') {
	activeTab = tab;
	if (tab === 'chat') {
		chatStarted = true;
		topologyMinimized = true;
	} else {
		topologyMinimized = false;
	}
}

// ─── Device Selection ────────────────────────────────────────────────

export function toggleDeviceSelection(serial: string) {
	const next = new Set(selectedSerials);
	if (next.has(serial)) {
		next.delete(serial);
	} else {
		next.add(serial);
	}
	selectedSerials = next;
	activeGroupId = null; // manual selection clears active group
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
	const group: DeviceGroup = {
		id,
		name,
		serials: Array.from(selectedSerials),
		createdAt: Date.now(),
	};
	deviceGroups = [...deviceGroups, group];
	saveGroups();
	return id;
}

export function deleteGroup(id: string) {
	deviceGroups = deviceGroups.filter((g) => g.id !== id);
	if (activeGroupId === id) activeGroupId = null;
	saveGroups();
}

export function renameGroup(id: string, name: string) {
	const group = deviceGroups.find((g) => g.id === id);
	if (group) {
		group.name = name;
		deviceGroups = [...deviceGroups];
		saveGroups();
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

export function loadGroup(id: string) {
	const group = deviceGroups.find((g) => g.id === id);
	if (group) {
		selectedSerials = new Set(group.serials);
		activeGroupId = id;
	}
}

export function startPolling() {
	if (!browser || pollInterval) return;
	pollInterval = setInterval(poll, POLL_MS);
	poll(); // immediate first
}

export function stopPolling() {
	if (pollInterval) {
		clearInterval(pollInterval);
		pollInterval = null;
	}
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
		lastPollTime = Date.now();
	} catch (e) {
		console.warn('Poll failed:', e);
	}
}

export async function refreshDevices() {
	try {
		const resp: DevicesResponse = await fetch('/api/devices/refresh', { method: 'POST' }).then(
			(r) => r.json()
		);
		devices = resp.devices;
		readyCount = resp.ready_count;
	} catch (e) {
		console.error('Refresh failed:', e);
	}
}

export async function fetchModels() {
	try {
		modelsData = await fetch('/api/models').then((r) => r.json());
	} catch (e) {
		console.error('Models fetch failed:', e);
	}
}

export async function startRing(config: {
	model_path: string;
	devices?: string;
	total_layers?: number;
	context_size?: number;
}) {
	const resp = await fetch('/api/ring/start', {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify(config),
	});
	if (!resp.ok) throw new Error(await resp.text());
	await poll();
}

export async function stopRing() {
	const resp = await fetch('/api/ring/stop', { method: 'POST' });
	if (!resp.ok) throw new Error(await resp.text());
	await poll();
}

export async function downloadModel(params: { repo_id?: string; filename?: string; url?: string }) {
	const resp = await fetch('/api/models/download', {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify(params),
	});
	return resp.json();
}

export async function distributeModel(model_name: string, target_devices = 'all') {
	const resp = await fetch('/api/models/distribute', {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify({ model_name, devices: target_devices }),
	});
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
	} catch (e) {
		console.warn('Job poll failed:', e);
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

	// Add user message
	const userMsg: ChatMessage = {
		id: generateId(),
		role: 'user',
		content,
		timestamp: Date.now(),
	};
	conv.messages = [...conv.messages, userMsg];

	// Add placeholder assistant message
	const assistantMsg: ChatMessage = {
		id: generateId(),
		role: 'assistant',
		content: '',
		timestamp: Date.now(),
	};
	conv.messages = [...conv.messages, assistantMsg];
	conv.updatedAt = Date.now();
	saveConversations();

	// Stream response
	streaming = true;
	const startTime = Date.now();
	let tokenCount = 0;
	let firstTokenTime = 0;

	try {
		const resp = await fetch('/v1/chat/completions', {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({
				messages: conv.messages
					.filter((m) => m.role !== 'system' && m.id !== assistantMsg.id)
					.map((m) => ({ role: m.role, content: m.content })),
				stream: true,
				temperature: 0.7,
				max_tokens: 2048,
			}),
		});

		if (!resp.ok || !resp.body) {
			assistantMsg.content = `Error: ${resp.statusText}`;
			saveConversations();
			streaming = false;
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
						assistantMsg.content += delta;
						// Trigger reactivity
						conv.messages = [...conv.messages];
					}
				} catch { /* skip malformed */ }
			}
		}

		assistantMsg.ttftMs = firstTokenTime;
		const elapsed = (Date.now() - startTime) / 1000;
		assistantMsg.tps = elapsed > 0 ? tokenCount / elapsed : 0;
	} catch (e) {
		assistantMsg.content += `\n\n[Error: ${e}]`;
	} finally {
		streaming = false;
		conv.updatedAt = Date.now();
		saveConversations();
	}
}

// Auto-name conversation from first message
export function autoNameConversation(convId: string) {
	const conv = conversations.find((c) => c.id === convId);
	if (!conv || conv.messages.length === 0) return;
	const first = conv.messages[0].content;
	conv.name = first.slice(0, 40) + (first.length > 40 ? '...' : '');
	saveConversations();
}

// Initialize
if (browser) {
	loadConversations();
	loadGroups();
}
