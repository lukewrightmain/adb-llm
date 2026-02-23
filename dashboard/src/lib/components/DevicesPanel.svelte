<script lang="ts">
	import {
		getDevices,
		getReadyCount,
		getRingStatus,
		getSelectedSerials,
		getIsRefreshing,
		toggleDeviceSelection,
		selectAllDevices,
		clearSelection,
		refreshDevices,
		isDeviceSelected,
	} from '$lib/stores/app.svelte';

	const devices = $derived(getDevices());
	const readyCount = $derived(getReadyCount());
	const ring = $derived(getRingStatus());
	const selectedSerials = $derived(getSelectedSerials());
	const refreshing = $derived(getIsRefreshing());

	// Pull-to-refresh
	let touchStartY = 0;
	let pullDist = $state(0);
	let pulling = $state(false);

	function onTouchStart(e: TouchEvent) {
		const el = e.currentTarget as HTMLElement;
		if (el.scrollTop === 0) {
			touchStartY = e.touches[0].clientY;
			pulling = true;
		}
	}

	function onTouchMove(e: TouchEvent) {
		if (!pulling) return;
		const dy = e.touches[0].clientY - touchStartY;
		pullDist = Math.max(0, Math.min(dy * 0.4, 80));
	}

	async function onTouchEnd() {
		if (pullDist > 50) {
			await refreshDevices();
		}
		pullDist = 0;
		pulling = false;
	}

	function stateColor(state: string): string {
		switch (state) {
			case 'ready': return 'bg-success';
			case 'busy': return 'bg-warning';
			case 'error': return 'bg-error';
			case 'connected': return 'bg-primary';
			default: return 'bg-muted';
		}
	}

	function thermalColor(temp: number): string {
		if (temp > 42) return 'text-error';
		if (temp > 38) return 'text-warning';
		return 'text-muted';
	}

	function ramPercent(dev: { available_ram_mb: number; total_ram_mb: number }): number {
		if (!dev.total_ram_mb) return 0;
		return Math.round((dev.available_ram_mb / dev.total_ram_mb) * 100);
	}

	function isInRing(serial: string): boolean {
		return ring.nodes.some((n) => n.serial === serial);
	}
</script>

<div
	class="h-full overflow-y-auto"
	ontouchstart={onTouchStart}
	ontouchmove={onTouchMove}
	ontouchend={onTouchEnd}
>
	<!-- Pull indicator -->
	{#if pullDist > 0}
		<div class="flex items-center justify-center" style="height: {pullDist}px">
			<div class="w-5 h-5 border-2 border-primary border-t-transparent rounded-full spinner"></div>
		</div>
	{/if}

	<!-- Header -->
	<div class="sticky top-0 z-10 bg-background/95 backdrop-blur border-b border-border px-4 py-3">
		<div class="flex items-center justify-between">
			<div>
				<span class="text-sm font-bold">{devices.length} Devices</span>
				<span class="text-xs text-muted ml-2">{readyCount} ready</span>
			</div>
			<div class="flex gap-2">
				{#if selectedSerials.size > 0}
					<button
						class="px-3 py-1.5 text-xs rounded-lg bg-surface border border-border text-muted active:bg-surface-hover min-h-[36px]"
						onclick={clearSelection}
					>Clear ({selectedSerials.size})</button>
				{:else}
					<button
						class="px-3 py-1.5 text-xs rounded-lg bg-surface border border-border text-muted active:bg-surface-hover min-h-[36px]"
						onclick={selectAllDevices}
					>Select All</button>
				{/if}
				<button
					class="px-3 py-1.5 text-xs rounded-lg bg-primary/10 text-primary active:bg-primary/20 min-h-[36px]"
					onclick={refreshDevices}
					disabled={refreshing}
				>
					{refreshing ? 'Scanning...' : 'Refresh'}
				</button>
			</div>
		</div>
	</div>

	<!-- Device cards -->
	<div class="p-3 grid grid-cols-1 sm:grid-cols-2 gap-2">
		{#each devices as dev (dev.serial)}
			{@const selected = isDeviceSelected(dev.serial)}
			{@const inRing = isInRing(dev.serial)}
			{@const ringNode = ring.nodes.find((n) => n.serial === dev.serial)}
			<button
				class="w-full text-left p-3 rounded-lg border transition-colors
					{selected ? 'border-primary bg-primary/5' : 'border-border bg-surface'}
					active:bg-surface-hover min-h-[64px]"
				onclick={() => toggleDeviceSelection(dev.serial)}
			>
				<div class="flex items-center gap-2 mb-1.5">
					<!-- State dot -->
					<div class="w-2.5 h-2.5 rounded-full {stateColor(dev.state)} shrink-0 {dev.state === 'busy' ? 'status-pulse' : ''}"></div>

					<!-- Serial -->
					<span class="text-xs font-bold truncate flex-1">{dev.short_serial}</span>

					<!-- Checkmark if selected -->
					{#if selected}
						<svg class="w-4 h-4 text-primary shrink-0" viewBox="0 0 20 20" fill="currentColor">
							<path fill-rule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clip-rule="evenodd"/>
						</svg>
					{/if}
				</div>

				<!-- Model name -->
				<div class="text-[10px] text-muted truncate mb-1.5">{dev.model}</div>

				<!-- Info row -->
				<div class="flex items-center gap-3 text-[10px]">
					<!-- RAM bar -->
					<div class="flex items-center gap-1 flex-1 min-w-0">
						<span class="text-muted shrink-0">RAM</span>
						<div class="flex-1 h-1.5 bg-border rounded-full overflow-hidden">
							<div
								class="h-full rounded-full {ramPercent(dev) > 50 ? 'bg-success' : ramPercent(dev) > 25 ? 'bg-warning' : 'bg-error'}"
								style="width: {ramPercent(dev)}%"
							></div>
						</div>
						<span class="text-muted shrink-0">{ramPercent(dev)}%</span>
					</div>

					<!-- Thermal -->
					<span class="{thermalColor(dev.thermal_temp_c)} shrink-0">{dev.thermal_temp_c}°</span>

					<!-- Ring badge -->
					{#if inRing}
						<span class="px-1.5 py-0.5 rounded text-[9px] bg-primary/15 text-primary shrink-0">
							R{ringNode?.rank}
						</span>
					{/if}

					<!-- Model count -->
					{#if dev.models.length > 0}
						<span class="text-muted shrink-0">{dev.models.length}M</span>
					{/if}
				</div>
			</button>
		{/each}

		{#if devices.length === 0}
			<div class="col-span-full text-center text-muted text-xs py-12">
				No devices discovered. Tap Refresh to scan.
			</div>
		{/if}
	</div>
</div>
