<script lang="ts">
	import {
		getManagedDevices,
		getSelectedSerials,
		getDeviceGroups,
		getActiveGroupId,
		getSettings,
		getRingHealth,
		isRingActive,
		isRingForming,
		getRingFormationProgress,
		getRingMasterDevice,
		getRingChatTemplate,
		setChatTemplate,
		toggleDeviceSelection,
		selectAllDevices,
		clearSelection,
		loadGroup,
		handleStartRing,
		handleStopRing,
		setActiveTab,
		refreshModels,
		updateSettings,
	} from '$lib/stores/app.svelte';
	import type { RingFormationConfig } from '$lib/types/adb';
	import { CHAT_TEMPLATES } from '$lib/types';
	import ProgressBar from './shared/ProgressBar.svelte';
	import { onMount } from 'svelte';

	const devices = $derived(getManagedDevices());
	const selectedSerials = $derived(getSelectedSerials());
	const groups = $derived(getDeviceGroups());
	const activeGroupId = $derived(getActiveGroupId());
	const ringSettings = $derived(getSettings());
	const health = $derived(getRingHealth());
	const active = $derived(isRingActive());
	const progress = $derived(getRingFormationProgress());
	const masterDevice = $derived(getRingMasterDevice());

	const readyDevices = $derived(devices.filter(d => d.state === 'ready'));
	const selectedDevices = $derived(
		selectedSerials.size > 0
			? devices.filter(d => selectedSerials.has(d.serial) && d.state === 'ready')
			: readyDevices
	);

	// Get available models from first selected device
	const availableModels = $derived(selectedDevices[0]?.models ?? []);

	// Form state
	let modelPath = $state('');
	let draftModelPath = $state('');
	let useDraft = $state(true);
	let draftMax = $state(24);
	let totalLayers = $state(62);
	let contextSize = $state(2048);
	let chatTemplate = $state('');
	let starting = $state(false);
	let stopping = $state(false);
	let showDevicePicker = $state(false);
	let error = $state('');
	let initialized = $state(false);
	let switchingTemplate = $state(false);
	let templateSwitchResult = $state<string | null>(null);
	let showSampling = $state(false);

	const currentTemplate = $derived(getRingChatTemplate());
	const chatTemplateDesc = $derived(CHAT_TEMPLATES.find(t => t.id === chatTemplate)?.description ?? '');

	onMount(() => { if (!isRingForming()) refreshModels(); });

	// Sync form from settings once
	$effect(() => {
		if (!initialized && ringSettings) {
			modelPath = ringSettings.defaultModel || '';
			draftModelPath = ringSettings.defaultDraftModel || '';
			useDraft = ringSettings.speculative;
			draftMax = ringSettings.draftMax;
			totalLayers = ringSettings.totalLayers;
			contextSize = ringSettings.contextSize;
			chatTemplate = ringSettings.chatTemplate || '';
			initialized = true;
		}
	});

	async function handleStart() {
		if (!modelPath) { error = 'Select a model'; return; }
		if (selectedDevices.length < 2) { error = 'Need at least 2 ready devices'; return; }
		error = '';
		starting = true;

		try {
			const config: RingFormationConfig = {
				devices: selectedDevices,
				modelPath,
				draftModelPath: useDraft ? draftModelPath : undefined,
				draftMax,
				totalLayers,
				contextSize,
				threads: 4,
				taskset: 'f0',
				prefetch: ringSettings.prefetch,
				httpPort: 8080,
				dataPort: ringSettings.dataPort || 9100,
				signalPort: ringSettings.signalPort || 10100,
				adbPath: ringSettings.adbPath || undefined,
				chatTemplate: chatTemplate || undefined,
			};
			await handleStartRing(config);
		} catch (e) {
			error = `${e instanceof Error ? e.message : e}`;
		} finally {
			starting = false;
		}
	}

	async function handleStop() {
		stopping = true;
		try { await handleStopRing(); }
		finally { stopping = false; }
	}

	function thermalColor(temp: number | null): string {
		if (temp === null) return 'text-muted';
		if (temp > 42) return 'text-error';
		if (temp > 38) return 'text-warning';
		return 'text-muted';
	}
</script>

<div class="h-full overflow-y-auto">
	{#if !active}
		<!-- ── Start Form ── -->
		<div class="p-4 space-y-4">
			<h2 class="text-sm font-bold">Start Ring</h2>

			{#if error}
				<div class="p-2 rounded-lg bg-error/10 text-error text-xs">{error}</div>
			{/if}

			<!-- Model selector -->
			<div>
				<label class="block text-xs text-muted mb-1">Target Model</label>
				<select bind:value={modelPath} class="w-full bg-surface border border-border rounded-lg px-3 py-2.5 text-xs text-foreground min-h-[44px]">
					<option value="">Select model...</option>
					{#each availableModels as m}
						<option value={m}>{m}</option>
					{/each}
				</select>
				{#if availableModels.length === 0}
					<p class="text-[10px] text-warning mt-1">No models on selected devices. Deploy models first.</p>
				{/if}
			</div>

			<!-- Draft toggle -->
			<div>
				<label class="flex items-center gap-2 min-h-[44px]">
					<input type="checkbox" bind:checked={useDraft} class="w-5 h-5 rounded accent-primary" />
					<span class="text-xs">Speculative decoding (draft model)</span>
				</label>
			</div>

			{#if useDraft}
				<div>
					<label class="block text-xs text-muted mb-1">Draft Model</label>
					<select bind:value={draftModelPath} class="w-full bg-surface border border-border rounded-lg px-3 py-2.5 text-xs text-foreground min-h-[44px]">
						<option value="">Select draft model...</option>
						{#each availableModels as m}
							<option value={m}>{m}</option>
						{/each}
					</select>
				</div>

				<div>
					<label class="block text-xs text-muted mb-1">Draft Max Tokens</label>
					<input type="number" bind:value={draftMax} min={1} max={64}
						class="w-full bg-surface border border-border rounded-lg px-3 py-2.5 text-xs text-foreground min-h-[44px]" />
				</div>
			{/if}

			<!-- Layers + Context -->
			<div class="grid grid-cols-2 gap-3">
				<div>
					<label class="block text-xs text-muted mb-1">Layers</label>
					<input type="number" bind:value={totalLayers} min={1} max={200}
						class="w-full bg-surface border border-border rounded-lg px-3 py-2.5 text-xs text-foreground min-h-[44px]" />
				</div>
				<div>
					<label class="block text-xs text-muted mb-1">Context</label>
					<input type="number" bind:value={contextSize} min={512} max={32768} step={512}
						class="w-full bg-surface border border-border rounded-lg px-3 py-2.5 text-xs text-foreground min-h-[44px]" />
				</div>
			</div>

			<!-- Chat Template -->
			<div>
				<label class="block text-xs text-muted mb-1">Chat Template</label>
				<select bind:value={chatTemplate} class="w-full bg-surface border border-border rounded-lg px-3 py-2.5 text-xs text-foreground min-h-[44px]">
					{#each CHAT_TEMPLATES as t}
						<option value={t.id}>{t.name}</option>
					{/each}
				</select>
				{#if chatTemplateDesc}
					<p class="text-[10px] text-muted mt-1">{chatTemplateDesc}</p>
				{/if}
			</div>

			<!-- Device picker -->
			<div>
				<button
					class="w-full flex items-center justify-between px-3 py-2.5 bg-surface border border-border rounded-lg text-xs min-h-[44px]"
					onclick={() => showDevicePicker = !showDevicePicker}
				>
					<span class="text-muted">
						Devices: {selectedSerials.size === 0 ? `All (${readyDevices.length} ready)` : `${selectedDevices.length} selected`}
					</span>
					<svg class="w-4 h-4 text-muted transition-transform {showDevicePicker ? 'rotate-180' : ''}" viewBox="0 0 20 20" fill="currentColor">
						<path fill-rule="evenodd" d="M5.293 7.293a1 1 0 011.414 0L10 10.586l3.293-3.293a1 1 0 111.414 1.414l-4 4a1 1 0 01-1.414 0l-4-4a1 1 0 010-1.414z" clip-rule="evenodd"/>
					</svg>
				</button>

				{#if showDevicePicker}
					<div class="mt-2 p-2 bg-surface border border-border rounded-lg space-y-2">
						{#if groups.length > 0}
							<div class="flex flex-wrap gap-1">
								<button
									class="px-2 py-1 text-[10px] rounded-full border min-h-[32px]
										{selectedSerials.size === 0 ? 'border-primary bg-primary/10 text-primary' : 'border-border text-muted'}"
									onclick={clearSelection}
								>All</button>
								{#each groups as g (g.id)}
									<button
										class="px-2 py-1 text-[10px] rounded-full border min-h-[32px]
											{activeGroupId === g.id ? 'border-primary bg-primary/10 text-primary' : 'border-border text-muted'}"
										onclick={() => loadGroup(g.id)}
									>{g.name} ({g.serials.length})</button>
								{/each}
							</div>
						{/if}

						<div class="flex gap-2 text-[10px]">
							<button class="text-primary" onclick={selectAllDevices}>Select all</button>
							<button class="text-muted" onclick={clearSelection}>Clear</button>
						</div>

						<div class="max-h-40 overflow-y-auto space-y-1">
							{#each devices as dev (dev.serial)}
								{@const sel = selectedSerials.has(dev.serial)}
								<button
									class="w-full flex items-center gap-2 px-2 py-1.5 rounded text-[10px] min-h-[36px]
										{sel ? 'bg-primary/5 text-foreground' : 'text-muted'}
										{dev.state !== 'ready' ? 'opacity-40' : ''}"
									onclick={() => toggleDeviceSelection(dev.serial)}
								>
									<div class="w-3 h-3 rounded border {sel ? 'bg-primary border-primary' : 'border-border'}"></div>
									<span class="truncate">{dev.shortSerial}</span>
									<span class="text-muted ml-auto">{dev.info?.model ?? dev.state}</span>
								</button>
							{/each}
						</div>
					</div>
				{/if}
			</div>

			<!-- Start button -->
			<button
				class="w-full py-3.5 rounded-xl text-sm font-bold transition-colors min-h-[56px]
					{starting ? 'bg-primary/50 text-foreground/50' : 'bg-primary text-background active:bg-primary-dim'}"
				onclick={handleStart}
				disabled={starting}
			>
				{#if starting && progress}
					<div class="space-y-2 px-4">
						<div class="flex items-center justify-center gap-2">
							<div class="w-4 h-4 border-2 border-current border-t-transparent rounded-full spinner"></div>
							<span class="text-xs">{progress.message}</span>
						</div>
						<ProgressBar progress={progress.progress} />
					</div>
				{:else if starting}
					<div class="flex items-center justify-center gap-2">
						<div class="w-4 h-4 border-2 border-current border-t-transparent rounded-full spinner"></div>
						Starting...
					</div>
				{:else}
					Start Ring ({selectedDevices.length} phones)
				{/if}
			</button>
		</div>

	{:else}
		<!-- ── Active Ring ── -->
		<div class="p-4 space-y-4">
			<!-- Status banner -->
			{#if health.ready}
				<div class="p-3 rounded-xl bg-success/10 border border-success/20">
					<div class="flex items-center gap-2 mb-2">
						<div class="w-3 h-3 rounded-full bg-success"></div>
						<span class="text-sm font-bold text-success">Ready</span>
					</div>
					<p class="text-[10px] text-muted mb-3">Ring operational on {masterDevice?.info?.ipAddress ?? 'master'}</p>
					<button
						class="w-full py-2.5 rounded-lg text-xs font-bold bg-success text-background active:bg-success/80 min-h-[44px]"
						onclick={() => setActiveTab('chat')}
					>Open Chat</button>
				</div>
			{:else}
				<div class="p-3 rounded-xl bg-warning/10 border border-warning/20">
					<div class="flex items-center gap-2 mb-1">
						<div class="w-3 h-3 border-2 border-warning border-t-transparent rounded-full spinner"></div>
						<span class="text-sm font-bold text-warning">{health.status === 'reconnecting' ? 'Reconnecting' : 'Loading'}</span>
					</div>
					{#if health.status === 'reconnecting'}
						<p class="text-[10px] text-muted">Ring is running — reconnecting to devices...</p>
					{:else if progress}
						<p class="text-[10px] text-muted mb-2">{progress.message}</p>
						<ProgressBar progress={progress.progress} />
					{:else}
						<p class="text-[10px] text-muted">Waiting for ring to become ready...</p>
					{/if}
				</div>
			{/if}

			<!-- Master info -->
			{#if masterDevice?.info}
				<div class="p-3 bg-surface rounded-lg border border-border space-y-1">
					<div class="text-xs">
						<span class="text-muted">Master:</span>
						<span class="ml-1 text-foreground">{masterDevice.info.ipAddress}</span>
					</div>
					<div class="text-xs text-muted">
						{masterDevice.info.model} &middot; {masterDevice.info.chipset}
					</div>
				</div>
			{/if}

			<!-- Chat Template (runtime switch) -->
			{#if health.ready}
				<div class="p-3 bg-surface rounded-lg border border-border space-y-2">
					<div class="flex items-center justify-between">
						<span class="text-xs font-bold text-foreground">Chat Template</span>
						{#if currentTemplate !== null}
							<span class="text-[10px] text-muted px-1.5 py-0.5 bg-background rounded">
								{CHAT_TEMPLATES.find(t => t.id === currentTemplate)?.name || currentTemplate || 'Auto-detect'}
							</span>
						{/if}
					</div>
					<select
						value={currentTemplate ?? ''}
						onchange={async (e) => {
							const newTmpl = (e.target as HTMLSelectElement).value;
							switchingTemplate = true;
							templateSwitchResult = null;
							const ok = await setChatTemplate(newTmpl);
							switchingTemplate = false;
							templateSwitchResult = ok ? `Switched to ${CHAT_TEMPLATES.find(t => t.id === newTmpl)?.name || 'Auto-detect'}` : 'Failed to switch template';
							if (ok) setTimeout(() => { templateSwitchResult = null; }, 4000);
						}}
						disabled={switchingTemplate}
						class="w-full bg-background border border-border rounded-lg px-3 py-2.5 text-xs text-foreground min-h-[44px]"
					>
						{#each CHAT_TEMPLATES as t}
							<option value={t.id}>{t.name} — {t.description}</option>
						{/each}
					</select>
					{#if switchingTemplate}
						<div class="flex items-center gap-2 text-[10px] text-muted">
							<div class="w-2.5 h-2.5 border-2 border-current border-t-transparent rounded-full spinner"></div>
							Switching...
						</div>
					{/if}
					{#if templateSwitchResult}
						<p class="text-[10px] {templateSwitchResult.startsWith('Failed') ? 'text-error' : 'text-success'}">{templateSwitchResult}</p>
					{/if}
				</div>
			{/if}

			<!-- Sampling Controls (collapsible) -->
			{#if health.ready}
				<div class="p-3 bg-surface rounded-lg border border-border space-y-2">
					<button
						class="w-full flex items-center justify-between text-xs font-bold text-foreground min-h-[36px]"
						onclick={() => showSampling = !showSampling}
					>
						<span>Sampling</span>
						<svg class="w-4 h-4 text-muted transition-transform {showSampling ? 'rotate-180' : ''}" viewBox="0 0 20 20" fill="currentColor">
							<path fill-rule="evenodd" d="M5.293 7.293a1 1 0 011.414 0L10 10.586l3.293-3.293a1 1 0 111.414 1.414l-4 4a1 1 0 01-1.414 0l-4-4a1 1 0 010-1.414z" clip-rule="evenodd"/>
						</svg>
					</button>

					{#if showSampling}
						<div class="space-y-3 pt-1 fade-in">
							<!-- Seed -->
							<div>
								<div class="flex items-center justify-between mb-1">
									<label class="text-[10px] text-muted">Seed</label>
									<span class="text-[10px] text-muted font-mono">{ringSettings.seed === -1 ? 'random' : ringSettings.seed}</span>
								</div>
								<input type="number" value={ringSettings.seed} min={-1} max={999999}
									onchange={(e) => updateSettings({ seed: parseInt((e.target as HTMLInputElement).value) || -1 })}
									class="w-full bg-background border border-border rounded-lg px-3 py-2 text-xs text-foreground min-h-[40px]"
									placeholder="-1 = random" />
							</div>

							<!-- Temperature -->
							<div>
								<div class="flex items-center justify-between mb-1">
									<label class="text-[10px] text-muted">Temperature</label>
									<span class="text-[10px] text-muted font-mono">{ringSettings.temperature.toFixed(1)}</span>
								</div>
								<input type="range" min="0" max="2" step="0.1" value={ringSettings.temperature}
									oninput={(e) => updateSettings({ temperature: parseFloat((e.target as HTMLInputElement).value) })}
									class="w-full accent-primary" />
							</div>

							<!-- Top-K -->
							<div>
								<div class="flex items-center justify-between mb-1">
									<label class="text-[10px] text-muted">Top-K</label>
									<span class="text-[10px] text-muted font-mono">{ringSettings.topK}</span>
								</div>
								<input type="range" min="0" max="100" step="1" value={ringSettings.topK}
									oninput={(e) => updateSettings({ topK: parseInt((e.target as HTMLInputElement).value) })}
									class="w-full accent-primary" />
							</div>

							<!-- Top-P -->
							<div>
								<div class="flex items-center justify-between mb-1">
									<label class="text-[10px] text-muted">Top-P</label>
									<span class="text-[10px] text-muted font-mono">{ringSettings.topP.toFixed(2)}</span>
								</div>
								<input type="range" min="0" max="1" step="0.05" value={ringSettings.topP}
									oninput={(e) => updateSettings({ topP: parseFloat((e.target as HTMLInputElement).value) })}
									class="w-full accent-primary" />
							</div>

							<!-- Repeat Penalty -->
							<div>
								<div class="flex items-center justify-between mb-1">
									<label class="text-[10px] text-muted">Repeat Penalty</label>
									<span class="text-[10px] text-muted font-mono">{ringSettings.repeatPenalty.toFixed(1)}</span>
								</div>
								<input type="range" min="0" max="2" step="0.1" value={ringSettings.repeatPenalty}
									oninput={(e) => updateSettings({ repeatPenalty: parseFloat((e.target as HTMLInputElement).value) })}
									class="w-full accent-primary" />
							</div>

							<!-- Max Tokens -->
							<div>
								<div class="flex items-center justify-between mb-1">
									<label class="text-[10px] text-muted">Max Tokens</label>
									<span class="text-[10px] text-muted font-mono">{ringSettings.maxTokens}</span>
								</div>
								<input type="range" min="64" max="8192" step="64" value={ringSettings.maxTokens}
									oninput={(e) => updateSettings({ maxTokens: parseInt((e.target as HTMLInputElement).value) })}
									class="w-full accent-primary" />
							</div>

							<!-- Reset -->
							<button
								class="w-full py-2 rounded-lg text-[10px] text-muted border border-border active:bg-surface-hover min-h-[36px]"
								onclick={() => updateSettings({
									seed: -1, temperature: 0.7, topK: 40, topP: 0.9, minP: 0.0,
									repeatPenalty: 1.1, repeatLastN: 64, frequencyPenalty: 0.0,
									presencePenalty: 0.0, maxTokens: 2048,
								})}
							>Reset to Defaults</button>
						</div>
					{/if}
				</div>
			{/if}

			<!-- Node list -->
			<div class="space-y-1.5">
				<h3 class="text-xs text-muted font-bold">Ring Devices</h3>
				{#each devices.filter(d => d.ringRank !== null) as dev (dev.serial)}
					<div class="flex items-center gap-2 px-3 py-2 bg-surface rounded-lg border border-border text-xs min-h-[44px]">
						<span class="w-6 text-center text-muted font-bold">#{dev.ringRank}</span>
						<div class="w-2 h-2 rounded-full shrink-0 {dev.state === 'ready' || dev.state === 'busy' ? 'bg-success' : 'bg-error'}"></div>
						<span class="flex-1 truncate">
							{#if dev.ringRank === 0}
								<span class="text-primary">MASTER</span>
							{:else}
								{dev.shortSerial}
							{/if}
						</span>
						{#if dev.info?.thermalTempC}
							<span class="{thermalColor(dev.info.thermalTempC)}">{dev.info.thermalTempC}°</span>
						{/if}
					</div>
				{/each}
			</div>

			<!-- Stop button -->
			<button
				class="w-full py-3.5 rounded-xl text-sm font-bold transition-colors min-h-[56px]
					{stopping ? 'bg-error/50 text-foreground/50' : 'bg-error text-white active:bg-error/80'}"
				onclick={handleStop}
				disabled={stopping}
			>
				{stopping ? 'Stopping...' : 'Stop Ring'}
			</button>
		</div>
	{/if}
</div>
