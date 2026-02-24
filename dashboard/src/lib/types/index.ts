export interface Device {
	serial: string;
	short_serial: string;
	state: 'connected' | 'ready' | 'busy' | 'error' | 'offline';
	model: string;
	total_ram_mb: number;
	available_ram_mb: number;
	usable_ram_mb: number;
	storage_free_mb: number;
	cpu_cores: number;
	cpu_arch: string;
	android_version: string;
	thermal_temp_c: number;
	has_swarm_worker: boolean;
	models: string[];
}

export interface DevicesResponse {
	devices: Device[];
	count: number;
	ready_count: number;
}

export interface RingNode {
	rank: number;
	serial: string;
	is_host: boolean;
	data_port: number;
	running: boolean;
	layers: number | null;
	model: string | null;
	thermal_temp_c: number | null;
	available_ram_mb: number | null;
}

export interface RingStatus {
	active: boolean;
	world_size: number;
	model: string | null;
	draft_model: string | null;
	nodes: RingNode[];
}

export interface ModelFile {
	name: string;
	size_mb: number;
	path: string;
}

export interface ModelsResponse {
	local_models: ModelFile[];
	device_models: Record<string, string[]>;
}

export interface JobProgress {
	job_id: string;
	status: string;
	progress: number;
	message: string;
}

export interface ChatMessage {
	id: string;
	role: 'user' | 'assistant' | 'system';
	content: string;
	timestamp: number;
	thinking?: string;
	ttftMs?: number;
	tps?: number;
	/** Speculative decoding stats fetched from /api/spec after generation */
	specStats?: SpecStats;
}

export interface SpecStats {
	draftedTotal: number;
	acceptedTotal: number;
	acceptRatePct: number;
	tokPerS: number;
	specCycles: number;
	tokensPredictedTotal: number;
}

export interface Conversation {
	id: string;
	name: string;
	messages: ChatMessage[];
	createdAt: number;
	updatedAt: number;
}

export interface DeviceGroup {
	id: string;
	name: string;
	serials: string[];
	createdAt: number;
}

export type TabId = 'devices' | 'ring' | 'chat' | 'models' | 'settings';

export interface RingSettings {
	speculative: boolean;
	draftMax: number;
	totalLayers: number;
	contextSize: number;
	prefetch: boolean;
	defaultModel: string;
	defaultDraftModel: string;
	/** Path to ADB binary on the proxy server (e.g., /usr/bin/adb, ~/.local/bin/adb) */
	adbPath: string;
	/** Data port for ring ZMQ communication (default 9100) */
	dataPort: number;
	/** Signal port for ring ZMQ communication (default 10100) */
	signalPort: number;
}

export interface RingHealth {
	ready: boolean;
	status: 'inactive' | 'loading' | 'ready';
}

export interface RingStartConfig {
	model_path: string;
	devices?: string;
	total_layers?: number;
	context_size?: number;
	prefetch?: boolean;
	draft_model_path?: string;
	draft_max?: number;
}
