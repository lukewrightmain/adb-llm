<script lang="ts">
	import {
		getSettings,
		updateSettings,
		getManagedDevices,
	} from '$lib/stores/app.svelte';
	import { setBatteryLevel, resetBattery, cleanRam } from '$lib/services/adb-shell';
	import { cleanupOldSessions, configureProxy, testAdbPath, detectAdbPaths, getProxyConfig } from '$lib/services/ring-orchestrator';
	import { CHAT_TEMPLATES } from '$lib/types';
	import KeyManager from './KeyManager.svelte';
	import { onMount } from 'svelte';

	const settings = $derived(getSettings());
	const devices = $derived(getManagedDevices());
	const readyDevices = $derived(devices.filter(d => d.state === 'ready' && d.adb));
	const chatTemplateDesc = $derived(CHAT_TEMPLATES.find(t => t.id === settings.chatTemplate)?.description ?? '');

	// Get available models from first ready device
	const availableModels = $derived(
		devices.find(d => d.state === 'ready')?.models ?? []
	);

	// Action states: null | 'working' | 'done:message'
	let batteryAction = $state<string | null>(null);
	let ramAction = $state<string | null>(null);
	let ramProgress = $state('');
	let killAction = $state<string | null>(null);
	let killProgress = $state('');

	const isBatteryWorking = $derived(batteryAction === 'setting' || batteryAction === 'resetting');
	const isBatteryDone = $derived(batteryAction?.startsWith('done:') ?? false);
	const batteryResult = $derived(isBatteryDone ? batteryAction!.slice(5) : '');

	const isRamWorking = $derived(ramAction === 'cleaning');
	const isRamDone = $derived(ramAction?.startsWith('done:') ?? false);
	const ramResult = $derived(isRamDone ? ramAction!.slice(5) : '');

	const isKillWorking = $derived(killAction === 'killing');
	const isKillDone = $derived(killAction?.startsWith('done:') ?? false);
	const killResult = $derived(isKillDone ? killAction!.slice(5) : '');

	// ADB path state
	let adbPathInput = $state('');
	let adbTestResult = $state<string | null>(null);
	let adbTesting = $state(false);
	let adbDetecting = $state(false);
	let adbDetected = $state<{ path: string; version: string }[]>([]);
	let adbProxyCurrent = $state('');

	// Load proxy's current ADB path on mount
	onMount(async () => {
		try {
			const cfg = await getProxyConfig();
			adbProxyCurrent = cfg.adbPath || '';
			adbPathInput = settings.adbPath || '';
		} catch { /* proxy not reachable */ }
	});

	function toggle(key: 'speculative' | 'prefetch') {
		updateSettings({ [key]: !settings[key] });
	}

	function setNum(key: 'draftMax' | 'totalLayers' | 'contextSize', e: Event) {
		const val = parseInt((e.target as HTMLInputElement).value);
		if (!isNaN(val)) updateSettings({ [key]: val });
	}

	function setModel(key: 'defaultModel' | 'defaultDraftModel', e: Event) {
		updateSettings({ [key]: (e.target as HTMLSelectElement).value });
	}

	async function handleSetBattery100() {
		batteryAction = 'setting';
		let done = 0;
		for (const dev of readyDevices) {
			try {
				await setBatteryLevel(dev.adb!, 100);
				done++;
			} catch { /* skip */ }
		}
		batteryAction = `done:Set ${done} device${done !== 1 ? 's' : ''} to 100%`;
		setTimeout(() => { batteryAction = null; }, 6000);
	}

	async function handleResetBattery() {
		batteryAction = 'resetting';
		let done = 0;
		for (const dev of readyDevices) {
			try {
				await resetBattery(dev.adb!);
				done++;
			} catch { /* skip */ }
		}
		batteryAction = `done:Reset ${done} device${done !== 1 ? 's' : ''} to real values`;
		setTimeout(() => { batteryAction = null; }, 6000);
	}

	async function handleCleanRam() {
		ramAction = 'cleaning';
		let doneCount = 0;
		const total = readyDevices.length;
		ramProgress = `0/${total}`;

		// Run ALL devices in parallel — each with a 15s safety timeout
		const results = await Promise.allSettled(
			readyDevices.map(async (dev) => {
				const timeoutPromise = new Promise<{ freedMb: number }>((_, reject) =>
					setTimeout(() => reject(new Error('timeout')), 15000)
				);
				try {
					const result = await Promise.race([cleanRam(dev.adb!), timeoutPromise]);
					doneCount++;
					ramProgress = `${doneCount}/${total}`;
					return result;
				} catch {
					doneCount++;
					ramProgress = `${doneCount}/${total}`;
					return { freedMb: 0 };
				}
			})
		);

		let totalFreed = 0;
		let successCount = 0;
		for (const r of results) {
			if (r.status === 'fulfilled') {
				totalFreed += r.value.freedMb;
				successCount++;
			}
		}

		const freedStr = totalFreed >= 1024
			? `${(totalFreed / 1024).toFixed(1)} GB`
			: `${totalFreed} MB`;
		if (totalFreed > 0) {
			ramAction = `done:Freed ~${freedStr} across ${successCount} device${successCount !== 1 ? 's' : ''}`;
		} else {
			ramAction = `done:Cleaned ${successCount} device${successCount !== 1 ? 's' : ''}`;
		}
		ramProgress = '';
		setTimeout(() => { ramAction = null; }, 8000);
	}

	async function handleTestAdbPath() {
		const path = adbPathInput.trim();
		if (!path) { adbTestResult = 'error:Enter a path first'; return; }
		adbTesting = true;
		adbTestResult = null;
		try {
			const result = await testAdbPath(path);
			if (result.ok) {
				adbTestResult = `ok:${result.version}`;
			} else {
				adbTestResult = `error:${result.error}`;
			}
		} catch (e) {
			adbTestResult = `error:Proxy unreachable`;
		}
		adbTesting = false;
	}

	async function handleSetAdbPath() {
		const path = adbPathInput.trim();
		if (!path) return;
		adbTesting = true;
		adbTestResult = null;
		try {
			const resp = await fetch(`http://${location.hostname}:3002/config`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ adbPath: path }),
			});
			const result = await resp.json();
			if (result.ok) {
				updateSettings({ adbPath: path });
				adbProxyCurrent = path;
				adbTestResult = `ok:Set! ${result.version || ''}`;
			} else {
				adbTestResult = `error:${result.error}`;
			}
		} catch {
			adbTestResult = 'error:Proxy unreachable';
		}
		adbTesting = false;
	}

	async function handleDetectAdb() {
		adbDetecting = true;
		adbDetected = [];
		try {
			const result = await detectAdbPaths();
			adbDetected = result.found;
			adbProxyCurrent = result.current;
			if (result.found.length === 0) {
				adbTestResult = 'error:No ADB binary found on proxy server';
			}
		} catch {
			adbTestResult = 'error:Proxy unreachable';
		}
		adbDetecting = false;
	}

	async function handleKillOldSessions() {
		killAction = 'killing';
		const serials = devices
			.filter(d => d.state === 'ready' || d.state === 'connecting')
			.map(d => d.serial);
		const total = serials.length;
		killProgress = `0/${total}`;

		const results = await cleanupOldSessions(serials);

		let totalKilled = 0;
		let devicesWithProcesses = 0;
		for (let i = 0; i < results.length; i++) {
			killProgress = `${i + 1}/${total}`;
			totalKilled += results[i].killed;
			if (results[i].killed > 0) devicesWithProcesses++;
		}

		if (totalKilled > 0) {
			killAction = `done:Killed ${totalKilled} process${totalKilled !== 1 ? 'es' : ''} on ${devicesWithProcesses} device${devicesWithProcesses !== 1 ? 's' : ''}`;
		} else {
			killAction = `done:All ${total} devices were already clean`;
		}
		killProgress = '';
		setTimeout(() => { killAction = null; }, 8000);
	}
</script>

<div class="h-full overflow-y-auto">
	<div class="p-4 space-y-5 max-w-lg mx-auto">
		<h2 class="text-sm font-bold">Ring Settings</h2>
		<p class="text-[10px] text-muted -mt-3">Defaults for new rings. Saved automatically.</p>

		<!-- Default Model -->
		<div>
			<label class="block text-xs text-muted mb-1" for="set-model">Default Target Model</label>
			<select
				id="set-model"
				value={settings.defaultModel}
				onchange={(e) => setModel('defaultModel', e)}
				class="w-full bg-surface border border-border rounded-lg px-3 py-2.5 text-xs text-foreground min-h-[44px]"
			>
				<option value="">None (choose each time)</option>
				{#each availableModels as m}
					<option value={m}>{m}</option>
				{/each}
			</select>
		</div>

		<!-- Chat Template -->
		<div>
			<label class="block text-xs text-muted mb-1" for="set-chat-template">Chat Template</label>
			<select
				id="set-chat-template"
				value={settings.chatTemplate}
				onchange={(e) => updateSettings({ chatTemplate: (e.target as HTMLSelectElement).value })}
				class="w-full bg-surface border border-border rounded-lg px-3 py-2.5 text-xs text-foreground min-h-[44px]"
			>
				{#each CHAT_TEMPLATES as t}
					<option value={t.id}>{t.name}</option>
				{/each}
			</select>
			{#if chatTemplateDesc}
				<p class="text-[10px] text-muted mt-1">{chatTemplateDesc}</p>
			{/if}
		</div>

		<!-- Speculative Decoding -->
		<div class="p-3 bg-surface rounded-lg border border-border space-y-3">
			<label class="flex items-center gap-3 min-h-[44px]">
				<input
					type="checkbox"
					checked={settings.speculative}
					onchange={() => toggle('speculative')}
					class="w-5 h-5 rounded accent-primary"
				/>
				<div>
					<span class="text-xs font-bold">Speculative Decoding</span>
					<p class="text-[10px] text-muted">Draft model predicts tokens, ring verifies. ~2-6x faster.</p>
				</div>
			</label>

			{#if settings.speculative}
				<div>
					<label class="block text-xs text-muted mb-1" for="set-draft">Draft Model</label>
					<select
						id="set-draft"
						value={settings.defaultDraftModel}
						onchange={(e) => setModel('defaultDraftModel', e)}
						class="w-full bg-surface border border-border rounded-lg px-3 py-2.5 text-xs text-foreground min-h-[44px]"
					>
						<option value="">None</option>
						{#each availableModels as m}
							<option value={m}>{m}</option>
						{/each}
					</select>
				</div>

				<div>
					<label class="block text-xs text-muted mb-1" for="set-dmax">Draft Max Tokens</label>
					<div class="flex items-center gap-3">
						<input
							id="set-dmax"
							type="range" min={4} max={48} step={4}
							value={settings.draftMax}
							oninput={(e) => setNum('draftMax', e)}
							class="flex-1 accent-primary"
						/>
						<span class="text-xs w-8 text-center">{settings.draftMax}</span>
					</div>
				</div>
			{/if}
		</div>

		<!-- Layers -->
		<div>
			<label class="block text-xs text-muted mb-1" for="set-layers">Total Layers</label>
			<input
				id="set-layers"
				type="number"
				value={settings.totalLayers}
				onchange={(e) => setNum('totalLayers', e)}
				min={1} max={200}
				class="w-full bg-surface border border-border rounded-lg px-3 py-2.5 text-xs text-foreground min-h-[44px]"
			/>
			<p class="text-[10px] text-muted mt-1">DeepSeek 33B = 62, 7B = 32, 1.3B = 24</p>
		</div>

		<!-- Context Size -->
		<div>
			<label class="block text-xs text-muted mb-1">Context Size</label>
			<div class="flex gap-2">
				{#each [512, 1024, 2048, 4096] as val}
					<button
						class="flex-1 py-2 text-xs rounded-lg min-h-[40px] transition-colors
							{settings.contextSize === val ? 'bg-primary/15 text-primary border border-primary/30' : 'bg-surface border border-border text-muted'}"
						onclick={() => updateSettings({ contextSize: val })}
					>{val}</button>
				{/each}
			</div>
		</div>

		<!-- Prefetch -->
		<label class="flex items-center gap-3 min-h-[44px]">
			<input
				type="checkbox"
				checked={settings.prefetch}
				onchange={() => toggle('prefetch')}
				class="w-5 h-5 rounded accent-primary"
			/>
			<div>
				<span class="text-xs">Prefetch layers</span>
				<p class="text-[10px] text-muted">Overlap layer loading with computation.</p>
			</div>
		</label>

		<!-- ADB Path -->
		<div class="p-3 bg-surface rounded-lg border border-border space-y-3">
			<div>
				<span class="text-xs font-bold text-foreground">ADB Binary Path</span>
				<p class="text-[10px] text-muted mt-0.5">Path to the ADB binary on the <strong>proxy server</strong> machine. This is the machine running ws-proxy.mjs — not your browser PC.</p>
			</div>

			{#if adbProxyCurrent}
				<div class="flex items-center gap-2 text-[10px]">
					<span class="text-muted">Currently using:</span>
					<code class="bg-background px-1.5 py-0.5 rounded text-foreground">{adbProxyCurrent}</code>
				</div>
			{/if}

			<div class="flex gap-2">
				<input
					type="text"
					bind:value={adbPathInput}
					placeholder="/usr/bin/adb"
					class="flex-1 bg-background border border-border rounded-lg px-3 py-2.5 text-xs text-foreground min-h-[44px] font-mono placeholder:text-muted/50"
				/>
				<button
					class="px-3 py-2.5 rounded-lg text-xs font-bold min-h-[44px] bg-primary/10 text-primary border border-primary/20 active:bg-primary/20 whitespace-nowrap"
					onclick={handleSetAdbPath}
					disabled={adbTesting || !adbPathInput.trim()}
				>
					{#if adbTesting}
						<div class="w-3 h-3 border-2 border-current border-t-transparent rounded-full spinner"></div>
					{:else}
						Set
					{/if}
				</button>
			</div>

			<div class="flex gap-2">
				<button
					class="flex-1 py-2 rounded-lg text-[10px] font-bold min-h-[36px] bg-surface border border-border text-muted active:bg-surface-hover"
					onclick={handleTestAdbPath}
					disabled={adbTesting || !adbPathInput.trim()}
				>Test Path</button>
				<button
					class="flex-1 py-2 rounded-lg text-[10px] font-bold min-h-[36px] bg-surface border border-border text-muted active:bg-surface-hover"
					onclick={handleDetectAdb}
					disabled={adbDetecting}
				>
					{#if adbDetecting}
						<div class="flex items-center justify-center gap-1">
							<div class="w-2.5 h-2.5 border-2 border-current border-t-transparent rounded-full spinner"></div>
							Scanning...
						</div>
					{:else}
						Auto-Detect
					{/if}
				</button>
			</div>

			{#if adbTestResult}
				{@const isOk = adbTestResult.startsWith('ok:')}
				<div class="p-2 rounded-lg text-[10px] {isOk ? 'bg-success/10 text-success' : 'bg-error/10 text-error'}">
					{isOk ? adbTestResult.slice(3) : adbTestResult.slice(6)}
				</div>
			{/if}

			{#if adbDetected.length > 0}
				<div class="space-y-1">
					<span class="text-[10px] text-muted font-bold">Found on proxy server:</span>
					{#each adbDetected as d}
						<button
							class="w-full flex items-center justify-between px-2 py-1.5 rounded text-[10px] bg-background border border-border active:bg-surface-hover min-h-[32px]"
							onclick={() => { adbPathInput = d.path; adbTestResult = null; adbDetected = []; }}
						>
							<code class="text-foreground">{d.path}</code>
							<span class="text-muted ml-2 truncate">{d.version}</span>
						</button>
					{/each}
				</div>
			{/if}
		</div>

		<!-- Battery Controls -->
		{#if readyDevices.length > 0}
			<div class="p-3 bg-surface rounded-lg border border-border space-y-2">
				<span class="text-xs font-bold text-foreground">Battery</span>
				<p class="text-[10px] text-muted">Set battery level on all {readyDevices.length} connected device{readyDevices.length !== 1 ? 's' : ''}.</p>
				<div class="flex gap-2">
					<button
						class="flex-1 py-2.5 rounded-lg text-xs font-bold min-h-[40px] transition-all duration-300
							{isBatteryDone ? 'bg-success/20 text-success border border-success/30' : 'bg-success/10 text-success active:bg-success/20 border border-success/20'}"
						onclick={handleSetBattery100}
						disabled={isBatteryWorking}
					>
						{#if batteryAction === 'setting'}
							<div class="flex items-center justify-center gap-2">
								<div class="w-3 h-3 border-2 border-current border-t-transparent rounded-full spinner"></div>
								Setting...
							</div>
						{:else if isBatteryDone}
							<div class="flex items-center justify-center gap-1.5">
								<svg class="w-4 h-4" viewBox="0 0 20 20" fill="currentColor">
									<path fill-rule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clip-rule="evenodd"/>
								</svg>
								{batteryResult}
							</div>
						{:else}
							Set All to 100%
						{/if}
					</button>
					<button
						class="py-2.5 px-3 rounded-lg text-xs text-muted bg-surface border border-border active:bg-surface-hover min-h-[40px]"
						onclick={handleResetBattery}
						disabled={isBatteryWorking}
					>
						{#if batteryAction === 'resetting'}
							<div class="w-3 h-3 border-2 border-current border-t-transparent rounded-full spinner"></div>
						{:else}
							Reset
						{/if}
					</button>
				</div>
			</div>
		{/if}

		<!-- RAM Cleanup -->
		{#if readyDevices.length > 0}
			<div class="p-3 bg-surface rounded-lg border transition-all duration-300
				{isRamDone ? 'border-success/30 bg-success/5' : 'border-border'} space-y-2">
				<span class="text-xs font-bold text-foreground">RAM Cleanup</span>
				<p class="text-[10px] text-muted">Kill background apps, Samsung bloatware, and clear caches on all {readyDevices.length} device{readyDevices.length !== 1 ? 's' : ''}. Safe — won't touch cellswarm, ADB, or system services.</p>
				<button
					class="w-full py-3 rounded-lg text-xs font-bold min-h-[44px] transition-all duration-300
						{isRamDone
							? 'bg-success/20 text-success border border-success/30'
							: isRamWorking
								? 'bg-primary/10 text-primary border border-primary/20'
								: 'bg-primary/10 text-primary active:bg-primary/20 border border-primary/20'}"
					onclick={handleCleanRam}
					disabled={isRamWorking}
				>
					{#if isRamWorking}
						<div class="flex flex-col items-center gap-1">
							<div class="flex items-center gap-2">
								<div class="w-3.5 h-3.5 border-2 border-current border-t-transparent rounded-full spinner"></div>
								<span>Cleaning RAM...</span>
							</div>
							{#if ramProgress}
								<span class="text-[10px] opacity-70">{ramProgress}</span>
							{/if}
						</div>
					{:else if isRamDone}
						<div class="flex items-center justify-center gap-2">
							<svg class="w-5 h-5" viewBox="0 0 20 20" fill="currentColor">
								<path fill-rule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clip-rule="evenodd"/>
							</svg>
							<span>{ramResult}</span>
						</div>
					{:else}
						Clean RAM on All Devices
					{/if}
				</button>
			</div>
		{/if}

		<!-- Kill Old Sessions -->
		{#if devices.length > 0}
			<div class="p-3 bg-surface rounded-lg border transition-all duration-300
				{isKillDone ? 'border-warning/30 bg-warning/5' : 'border-border'} space-y-2">
				<span class="text-xs font-bold text-foreground">Kill Old Sessions</span>
				<p class="text-[10px] text-muted">Stop all cellswarm processes (master, workers, spec-workers) left over from previous runs on all {devices.length} device{devices.length !== 1 ? 's' : ''}. Frees RAM and ports.</p>
				<button
					class="w-full py-3 rounded-lg text-xs font-bold min-h-[44px] transition-all duration-300
						{isKillDone
							? 'bg-warning/20 text-warning border border-warning/30'
							: isKillWorking
								? 'bg-warning/10 text-warning border border-warning/20'
								: 'bg-warning/10 text-warning active:bg-warning/20 border border-warning/20'}"
					onclick={handleKillOldSessions}
					disabled={isKillWorking}
				>
					{#if isKillWorking}
						<div class="flex flex-col items-center gap-1">
							<div class="flex items-center gap-2">
								<div class="w-3.5 h-3.5 border-2 border-current border-t-transparent rounded-full spinner"></div>
								<span>Killing processes...</span>
							</div>
							{#if killProgress}
								<span class="text-[10px] opacity-70">{killProgress}</span>
							{/if}
						</div>
					{:else if isKillDone}
						<div class="flex items-center justify-center gap-2">
							<svg class="w-5 h-5" viewBox="0 0 20 20" fill="currentColor">
								<path fill-rule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clip-rule="evenodd"/>
							</svg>
							<span>{killResult}</span>
						</div>
					{:else}
						Kill All Old Sessions
					{/if}
				</button>
			</div>
		{/if}

		<!-- ADB Keys -->
		<div class="p-3 bg-surface rounded-lg border border-border">
			<KeyManager />
		</div>

		<!-- Info -->
		<div class="p-3 bg-surface/50 rounded-lg border border-border/50">
			<h3 class="text-[10px] font-bold text-muted mb-1">About CellSwarm</h3>
			<p class="text-[10px] text-muted leading-relaxed">
				WebUSB dashboard — no backend server. Browser communicates directly with phones via USB.
				Chat streams via HTTP over ADB sockets to the cellswarm-master binary.
			</p>
			<p class="text-[10px] text-muted leading-relaxed mt-2">
				Best: 12 phones, speculative d24, Q4_K_M + 1.3B draft, context 2048. Peak: 6.1 tok/s.
			</p>
		</div>
	</div>
</div>
