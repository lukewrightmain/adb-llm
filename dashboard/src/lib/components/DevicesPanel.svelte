<script lang="ts">
	import {
		getManagedDevices,
		getSelectedSerials,
		getIsRefreshing,
		toggleDeviceSelection,
		selectAllDevices,
		clearSelection,
		refreshAllDevices,
		addDevice,
		removeDevice,
		isDeviceSelected,
	} from '$lib/stores/app.svelte';
	import DeviceCard from './DeviceCard.svelte';

	const devices = $derived(getManagedDevices());
	const selectedSerials = $derived(getSelectedSerials());
	const refreshing = $derived(getIsRefreshing());
	const readyCount = $derived(devices.filter(d => d.state === 'ready').length);

	let addingDevice = $state(false);

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
			await refreshAllDevices();
		}
		pullDist = 0;
		pulling = false;
	}

	async function handleAddDevice() {
		addingDevice = true;
		try {
			await addDevice();
		} finally {
			addingDevice = false;
		}
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
				{:else if devices.length > 0}
					<button
						class="px-3 py-1.5 text-xs rounded-lg bg-surface border border-border text-muted active:bg-surface-hover min-h-[36px]"
						onclick={selectAllDevices}
					>Select All</button>
				{/if}
				<button
					class="px-3 py-1.5 text-xs rounded-lg bg-primary/10 text-primary active:bg-primary/20 min-h-[36px]"
					onclick={refreshAllDevices}
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
			<DeviceCard
				device={dev}
				selected={isDeviceSelected(dev.serial)}
				onclick={() => toggleDeviceSelection(dev.serial)}
			/>
		{/each}

		{#if devices.length === 0}
			<div class="col-span-full text-center text-muted text-xs py-8">
				No devices connected yet.
			</div>
		{/if}
	</div>

	<!-- Add device button -->
	<div class="px-3 pb-4">
		<button
			class="w-full py-3 rounded-xl border-2 border-dashed border-border text-xs text-muted active:border-primary active:text-primary transition-colors min-h-[48px]"
			onclick={handleAddDevice}
			disabled={addingDevice}
		>
			{#if addingDevice}
				<div class="flex items-center justify-center gap-2">
					<div class="w-3 h-3 border border-current border-t-transparent rounded-full spinner"></div>
					Waiting for USB selection...
				</div>
			{:else}
				+ Connect Phone via USB
			{/if}
		</button>
	</div>
</div>
