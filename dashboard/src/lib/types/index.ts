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
	/** Chat template name (e.g. "chatml", "deepseek", "llama3") */
	chatTemplate: string;
}

/** Known chat templates supported by cellswarm-master */
export const CHAT_TEMPLATES: { id: string; name: string; description: string }[] = [
	{ id: '',          name: 'Auto-detect',     description: 'Use template from model metadata' },
	{ id: 'chatml',    name: 'ChatML',           description: 'OpenAI-style <|im_start|>/<|im_end|> — DeepSeek, Qwen, Yi' },
	{ id: 'deepseek',  name: 'DeepSeek',         description: 'DeepSeek native — User/Assistant markers' },
	{ id: 'deepseek2', name: 'DeepSeek v2',      description: 'DeepSeek-V2/V3 format' },
	{ id: 'llama2',    name: 'Llama 2',           description: '[INST] / [/INST] markers — Llama 2, Code Llama' },
	{ id: 'llama3',    name: 'Llama 3',           description: '<|start_header_id|> markers — Llama 3, 3.1, 3.2' },
	{ id: 'phi3',      name: 'Phi-3',             description: '<|user|> / <|assistant|> — Microsoft Phi-3' },
	{ id: 'gemma',     name: 'Gemma',             description: '<start_of_turn> markers — Google Gemma' },
	{ id: 'gemma2',    name: 'Gemma 2',           description: 'Gemma 2 format' },
	{ id: 'mistral',   name: 'Mistral',           description: '[INST] style — Mistral, Mixtral' },
	{ id: 'zephyr',    name: 'Zephyr',            description: '<|user|> / <|assistant|> — Zephyr, StableLM' },
	{ id: 'vicuna',    name: 'Vicuna',            description: 'USER: / ASSISTANT: — Vicuna v1.1' },
	{ id: 'openchat',  name: 'OpenChat',          description: 'GPT4 Correct format — OpenChat 3.5' },
	{ id: 'monarch',   name: 'Monarch',           description: 'Monarch format' },
	{ id: 'command-r', name: 'Command-R',         description: 'Cohere Command-R format' },
	{ id: 'chatglm3',  name: 'ChatGLM3',          description: 'ChatGLM3 format — THUDM' },
	{ id: 'chatglm4',  name: 'ChatGLM4',          description: 'ChatGLM4/GLM-4 format' },
	{ id: 'minicpm',   name: 'MiniCPM',           description: 'MiniCPM format — OpenBMB' },
	{ id: 'exaone3',   name: 'EXAONE 3',          description: 'LG AI EXAONE 3.0 format' },
	{ id: 'orion',     name: 'Orion',             description: 'OrionStar format' },
];

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
