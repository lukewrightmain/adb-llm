<script lang="ts">
	import {
		getRingStatus,
		getModelsData,
		getSelectedSerials,
		getDeviceGroups,
		getActiveGroupId,
		getTargetDevicesString,
		startRing,
		stopRing,
		fetchModels,
		selectAllDevices,
		clearSelection,
		createGroup,
		deleteGroup,
		loadGroup,
		updateGroupSerials,
	} from '$lib/stores/app.svelte';

	const ring = $derived(getRingStatus());
	const models = $derived(getModelsData());
	const selected = $derived(getSelectedSerials());
	const groups = $derived(getDeviceGroups());
	const activeGroupId = $derived(getActiveGroupId());
	const targetString = $derived(getTargetDevicesString());

	let selectedModel = $state('');
	let totalLayers = $state(64);
	let contextSize = $state(2048);
	let loading = $state(false);
	let error = $state('');

	// Group management
	let showGroupPanel = $state(false);
	let newGroupName = $state('');
	let editingGroupId = $state<string | null>(null);
	let editingGroupName = $state('');

	async function handleStart() {
		if (!selectedModel) {
			error = 'Select a model first';
			return;
		}
		loading = true;
		error = '';
		try {
			await startRing({
				model_path: selectedModel,
				devices: targetString,
				total_layers: totalLayers,
				context_size: contextSize,
			});
		} catch (e) {
			error = String(e);
		} finally {
			loading = false;
		}
	}

	async function handleStop() {
		loading = true;
		error = '';
		try {
			await stopRing();
		} catch (e) {
			error = String(e);
		} finally {
			loading = false;
		}
	}

	function handleSaveGroup() {
		const name = newGroupName.trim();
		if (!name || selected.size === 0) return;
		createGroup(name);
		newGroupName = '';
		showGroupPanel = false;
	}

	function handleUpdateGroup(id: string) {
		updateGroupSerials(id);
	}

	$effect(() => {
		fetchModels();
	});
</script>

<div class="border border-border rounded-lg bg-surface p-4">
	<h3 class="text-sm font-bold text-foreground mb-3">Ring Control</h3>

	{#if ring.active}
		<div class="space-y-2 mb-3">
			<div class="text-xs text-muted">
				<span class="text-success font-bold">ACTIVE</span> &mdash; {ring.world_size} nodes
			</div>
			{#if ring.model}
				<div class="text-xs text-muted">Model: <span class="text-foreground">{ring.model}</span></div>
			{/if}
		</div>
		<button
			class="w-full px-3 py-2 rounded bg-error/20 text-error border border-error/30 text-sm hover:bg-error/30 transition-colors disabled:opacity-50"
			onclick={handleStop}
			disabled={loading}
		>
			{loading ? 'Stopping...' : 'Stop Ring'}
		</button>
	{:else}
		<div class="space-y-3 mb-3">
			<div>
				<label for="model-select" class="block text-[10px] text-muted mb-1 uppercase tracking-wider">Model Path</label>
				<select
					id="model-select"
					class="w-full bg-background border border-border rounded px-2 py-1.5 text-xs text-foreground"
					bind:value={selectedModel}
				>
					<option value="">Select model...</option>
					{#each models.local_models as m}
						<option value={m.path}>{m.name} ({m.size_mb}MB)</option>
					{/each}
				</select>
			</div>

			<div class="grid grid-cols-2 gap-2">
				<div>
					<label for="layers-input" class="block text-[10px] text-muted mb-1 uppercase tracking-wider">Layers</label>
					<input
						id="layers-input"
						type="number"
						class="w-full bg-background border border-border rounded px-2 py-1.5 text-xs text-foreground"
						bind:value={totalLayers}
						min={1}
						max={128}
					/>
				</div>
				<div>
					<label for="context-input" class="block text-[10px] text-muted mb-1 uppercase tracking-wider">Context</label>
					<input
						id="context-input"
						type="number"
						class="w-full bg-background border border-border rounded px-2 py-1.5 text-xs text-foreground"
						bind:value={contextSize}
						min={256}
						max={8192}
						step={256}
					/>
				</div>
			</div>

			<!-- Device selection display -->
			<div>
				<label class="block text-[10px] text-muted mb-1 uppercase tracking-wider">
					Devices
					<span class="text-foreground ml-1">
						{#if selected.size === 0}
							(all)
						{:else}
							({selected.size} selected)
						{/if}
					</span>
				</label>
				<div class="bg-background border border-border rounded px-2 py-1.5 text-xs text-foreground min-h-[28px] flex items-center flex-wrap gap-1">
					{#if selected.size === 0}
						<span class="text-muted">All devices — click devices below to select specific ones</span>
					{:else}
						{#each Array.from(selected) as serial}
							<span class="bg-primary/15 text-primary px-1.5 py-0.5 rounded text-[10px]">
								{serial.length > 12 ? serial.slice(0, 4) + '..' + serial.slice(-4) : serial}
							</span>
						{/each}
					{/if}
				</div>
				<div class="flex gap-1 mt-1">
					<button
						class="text-[10px] text-muted hover:text-primary transition-colors"
						onclick={selectAllDevices}
					>Select all</button>
					<span class="text-border">|</span>
					<button
						class="text-[10px] text-muted hover:text-primary transition-colors"
						onclick={clearSelection}
					>Clear</button>
					<span class="text-border">|</span>
					<button
						class="text-[10px] text-muted hover:text-primary transition-colors"
						onclick={() => showGroupPanel = !showGroupPanel}
					>Groups</button>
				</div>
			</div>

			<!-- Group panel -->
			{#if showGroupPanel}
				<div class="border border-border rounded bg-background p-2 space-y-2">
					<div class="text-[10px] text-muted uppercase tracking-wider font-bold">Device Groups</div>

					{#if groups.length > 0}
						<div class="space-y-1">
							{#each groups as group (group.id)}
								<div class="flex items-center gap-1 text-xs">
									<button
										class="flex-1 text-left px-2 py-1 rounded transition-colors truncate
											{activeGroupId === group.id
												? 'bg-primary/15 text-primary'
												: 'text-foreground hover:bg-surface-hover'}"
										onclick={() => loadGroup(group.id)}
										title="{group.serials.length} devices"
									>
										{group.name}
										<span class="text-muted text-[10px] ml-1">({group.serials.length})</span>
									</button>
									{#if activeGroupId === group.id && selected.size > 0}
										<button
											class="text-[10px] text-warning hover:text-foreground px-1 shrink-0"
											onclick={() => handleUpdateGroup(group.id)}
											title="Update group with current selection"
										>save</button>
									{/if}
									<button
										class="text-[10px] text-error hover:text-foreground px-1 shrink-0"
										onclick={() => deleteGroup(group.id)}
									>x</button>
								</div>
							{/each}
						</div>
					{:else}
						<div class="text-[10px] text-muted">No groups yet</div>
					{/if}

					<!-- Create new group from current selection -->
					{#if selected.size > 0}
						<div class="flex gap-1">
							<input
								type="text"
								class="flex-1 bg-surface border border-border rounded px-2 py-1 text-xs text-foreground"
								bind:value={newGroupName}
								placeholder="Group name..."
								onkeydown={(e) => { if (e.key === 'Enter') handleSaveGroup(); }}
							/>
							<button
								class="px-2 py-1 rounded bg-primary/20 text-primary text-[10px] hover:bg-primary/30 transition-colors disabled:opacity-50"
								onclick={handleSaveGroup}
								disabled={!newGroupName.trim()}
							>Save</button>
						</div>
						<div class="text-[10px] text-muted">Saves current {selected.size} selected device(s) as a group</div>
					{:else}
						<div class="text-[10px] text-muted">Select devices first, then save as group</div>
					{/if}
				</div>
			{/if}
		</div>

		<button
			class="w-full px-3 py-2 rounded bg-primary/20 text-primary border border-primary/30 text-sm hover:bg-primary/30 transition-colors disabled:opacity-50"
			onclick={handleStart}
			disabled={loading || !selectedModel}
		>
			{loading ? 'Starting...' : 'Start Ring'}
		</button>
	{/if}

	{#if error}
		<div class="mt-2 text-xs text-error bg-error/10 rounded px-2 py-1.5">{error}</div>
	{/if}
</div>
