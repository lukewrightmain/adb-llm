<script lang="ts">
	import {
		getRingStatus,
		getRingHealth,
		getModelsData,
		getDevices,
		getSelectedSerials,
		getDeviceGroups,
		getActiveGroupId,
		getTargetDevicesString,
		getSettings,
		toggleDeviceSelection,
		startRing,
		stopRing,
		fetchModels,
		loadGroup,
		clearSelection,
		selectAllDevices,
		setActiveTab,
	} from '$lib/stores/app.svelte';
	import { onMount } from 'svelte';
	import type { RingStartConfig } from '$lib/types';

	const ring = $derived(getRingStatus());
	const health = $derived(getRingHealth());
	const models = $derived(getModelsData());
	const devices = $derived(getDevices());
	const selectedSerials = $derived(getSelectedSerials());
	const groups = $derived(getDeviceGroups());
	const activeGroupId = $derived(getActiveGroupId());
	const ringSettings = $derived(getSettings());

	// Form state — initialized from settings
	let modelPath = $state('');
	let draftModelPath = $state('');
	let useDraft = $state(true);
	let draftMax = $state(24);
	let totalLayers = $state(62);
	let contextSize = $state(2048);
	let starting = $state(false);
	let startProgress = $state('');
	let stopping = $state(false);
	let showDevicePicker = $state(false);
	let error = $state('');
	let initialized = $state(false);

	onMount(() => {
		fetchModels();
	});

	// Sync form state from settings on first render
	$effect(() => {
		if (!initialized && ringSettings) {
			modelPath = ringSettings.defaultModel || '';
			draftModelPath = ringSettings.defaultDraftModel || '';
			useDraft = ringSettings.speculative;
			draftMax = ringSettings.draftMax;
			totalLayers = ringSettings.totalLayers;
			contextSize = ringSettings.contextSize;
			initialized = true;
		}
	});

	async function handleStart() {
		if (!modelPath) { error = 'Select a model'; return; }
		error = '';
		starting = true;
		startProgress = 'Launching...';

		try {
			const config: RingStartConfig = {
				model_path: modelPath,
				total_layers: totalLayers,
				context_size: contextSize,
				prefetch: ringSettings.prefetch,
			};
			const targetStr = getTargetDevicesString();
			if (targetStr !== 'all') config.devices = targetStr;
			if (useDraft && draftModelPath) {
				config.draft_model_path = draftModelPath;
				config.draft_max = draftMax;
			}
			// This returns immediately — the backend runs the start in background
			await startRing(config);
		} catch (e) {
			error = `${e}`;
			starting = false;
			startProgress = '';
			return;
		}

		// Poll /api/ring/launch-status until done/failed
		const progressTimer = setInterval(async () => {
			try {
				const r = await fetch('/api/ring/launch-status');
				if (!r.ok) return;
				const d = await r.json();
				if (d.message) startProgress = d.message;
				if (d.status === 'done' || d.status === 'failed') {
					clearInterval(progressTimer);
					if (d.status === 'failed') {
						error = d.error || 'Ring start failed';
					}
					starting = false;
					startProgress = '';
				}
			} catch { /* ignore */ }
		}, 2000);
	}

	async function handleStop() {
		stopping = true;
		try { await stopRing(); } catch { /* handled in store */ }
		finally { stopping = false; }
	}

	function healthBadgeClass(): string {
		switch (health.status) {
			case 'ready': return 'bg-success/15 text-success';
			case 'loading': return 'bg-warning/15 text-warning';
			default: return 'bg-muted/15 text-muted';
		}
	}

	function thermalColor(temp: number | null): string {
		if (temp === null) return 'text-muted';
		if (temp > 42) return 'text-error';
		if (temp > 38) return 'text-warning';
		return 'text-muted';
	}
</script>

<div class="h-full overflow-y-auto">
	{#if !ring.active}
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
					{#each models.local_models as m}
						<option value={m.name}>{m.name} ({m.size_mb}MB)</option>
					{/each}
				</select>
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
						{#each models.local_models as m}
							<option value={m.name}>{m.name} ({m.size_mb}MB)</option>
						{/each}
					</select>
				</div>

				<div>
					<label class="block text-xs text-muted mb-1">Draft Max Tokens</label>
					<input type="number" bind:value={draftMax} min={1} max={64}
						class="w-full bg-surface border border-border rounded-lg px-3 py-2.5 text-xs text-foreground min-h-[44px]" />
				</div>
			{/if}

			<!-- Layers + Context side by side -->
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

			<!-- Device picker -->
			<div>
				<button
					class="w-full flex items-center justify-between px-3 py-2.5 bg-surface border border-border rounded-lg text-xs min-h-[44px]"
					onclick={() => showDevicePicker = !showDevicePicker}
				>
					<span class="text-muted">
						Devices: {selectedSerials.size === 0 ? 'All' : `${selectedSerials.size} selected`}
					</span>
					<svg class="w-4 h-4 text-muted transition-transform {showDevicePicker ? 'rotate-180' : ''}" viewBox="0 0 20 20" fill="currentColor">
						<path fill-rule="evenodd" d="M5.293 7.293a1 1 0 011.414 0L10 10.586l3.293-3.293a1 1 0 111.414 1.414l-4 4a1 1 0 01-1.414 0l-4-4a1 1 0 010-1.414z" clip-rule="evenodd"/>
					</svg>
				</button>

				{#if showDevicePicker}
					<div class="mt-2 p-2 bg-surface border border-border rounded-lg space-y-2">
						<!-- Groups -->
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

						<!-- Quick actions -->
						<div class="flex gap-2 text-[10px]">
							<button class="text-primary" onclick={selectAllDevices}>Select all</button>
							<button class="text-muted" onclick={clearSelection}>Clear</button>
						</div>

						<!-- Device list -->
						<div class="max-h-40 overflow-y-auto space-y-1">
							{#each devices as dev (dev.serial)}
								{@const sel = selectedSerials.has(dev.serial)}
								<button
									class="w-full flex items-center gap-2 px-2 py-1.5 rounded text-[10px] min-h-[36px]
										{sel ? 'bg-primary/5 text-foreground' : 'text-muted'}"
									onclick={() => toggleDeviceSelection(dev.serial)}
								>
									<div class="w-3 h-3 rounded border {sel ? 'bg-primary border-primary' : 'border-border'}"></div>
									<span class="truncate">{dev.short_serial}</span>
									<span class="text-muted ml-auto">{dev.model}</span>
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
				{#if starting}
					<div class="flex items-center justify-center gap-2">
						<div class="w-4 h-4 border-2 border-current border-t-transparent rounded-full spinner"></div>
						<span class="text-xs">{startProgress || 'Starting...'}</span>
					</div>
				{:else}
					Start Ring
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
					<p class="text-[10px] text-muted mb-3">{ring.world_size} nodes running &middot; {ring.model ?? 'unknown'}</p>
					<button
						class="w-full py-2.5 rounded-lg text-xs font-bold bg-success text-background active:bg-success/80 min-h-[44px]"
						onclick={() => setActiveTab('chat')}
					>Open Chat</button>
				</div>
			{:else}
				<div class="p-3 rounded-xl bg-warning/10 border border-warning/20">
					<div class="flex items-center gap-2 mb-1">
						<div class="w-3 h-3 border-2 border-warning border-t-transparent rounded-full spinner"></div>
						<span class="text-sm font-bold text-warning">Loading</span>
					</div>
					<p class="text-[10px] text-muted">
						Waiting for model to load on {ring.world_size} nodes...
					</p>
				</div>
			{/if}

			<!-- Model info -->
			<div class="p-3 bg-surface rounded-lg border border-border space-y-1">
				<div class="text-xs">
					<span class="text-muted">Model:</span>
					<span class="ml-1 text-foreground">{ring.model ?? 'unknown'}</span>
				</div>
				{#if ring.draft_model}
					<div class="text-xs">
						<span class="text-muted">Draft:</span>
						<span class="ml-1 text-foreground">{ring.draft_model}</span>
					</div>
				{/if}
				<div class="text-xs text-muted">{ring.world_size} nodes &middot; {ring.nodes.filter(n => n.running).length} running</div>
			</div>

			<!-- Node list -->
			<div class="space-y-1.5">
				<h3 class="text-xs text-muted font-bold">Nodes</h3>
				{#each ring.nodes as node (node.rank)}
					<div class="flex items-center gap-2 px-3 py-2 bg-surface rounded-lg border border-border text-xs min-h-[44px]">
						<!-- Rank -->
						<span class="w-6 text-center text-muted font-bold">#{node.rank}</span>

						<!-- Running dot -->
						<div class="w-2 h-2 rounded-full shrink-0 {node.running ? 'bg-success' : 'bg-error'}"></div>

						<!-- Serial / host label -->
						<span class="flex-1 truncate">
							{#if node.is_host}
								<span class="text-primary">HOST</span>
							{:else}
								{node.serial?.slice(-6) ?? '???'}
							{/if}
						</span>

						<!-- Layers -->
						{#if node.layers}
							<span class="text-muted">{node.layers}L</span>
						{/if}

						<!-- Thermal -->
						{#if node.thermal_temp_c !== null}
							<span class="{thermalColor(node.thermal_temp_c)}">{node.thermal_temp_c}°</span>
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
