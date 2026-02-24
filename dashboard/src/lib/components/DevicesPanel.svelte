<script lang="ts">
	import {
		getManagedDevices,
		getSelectedSerials,
		getIsRefreshing,
		getWebUsbSupported,
		getTcpProxyAvailable,
		toggleDeviceSelection,
		selectAllDevices,
		clearSelection,
		refreshAllDevices,
		addMultipleDevices,
		addTcpDevice,
		addMultipleTcpDevices,
		scanAndAddDevices,
		cancelScan,
		getScanProgress,
		isScanning,
		removeDevice,
		reconnectDevice,
		isDeviceSelected,
	} from '$lib/stores/app.svelte';
	import { parseIpRange, describeIpInput } from '$lib/utils/ip-range';
	import DeviceCard from './DeviceCard.svelte';

	const devices = $derived(getManagedDevices());
	const selectedSerials = $derived(getSelectedSerials());
	const refreshing = $derived(getIsRefreshing());
	const webUsbSupported = $derived(getWebUsbSupported());
	const tcpProxyAvailable = $derived(getTcpProxyAvailable());
	const readyCount = $derived(devices.filter(d => d.state === 'ready').length);
	const usbCount = $derived(devices.filter(d => d.transport === 'usb').length);
	const tcpCount = $derived(devices.filter(d => d.transport === 'tcp').length);
	const scanning = $derived(isScanning());
	const progress = $derived(getScanProgress());

	let addingUsb = $state(false);
	let addingTcp = $state(false);
	let showTcpInput = $state(false);
	let tcpInput = $state('');
	let tcpError = $state('');

	const parsedCount = $derived(tcpInput.trim() ? parseIpRange(tcpInput).length : 0);
	const isScannable = $derived(parsedCount > 10);

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

	async function handleAddUsb() {
		addingUsb = true;
		try {
			await addMultipleDevices();
		} finally {
			addingUsb = false;
		}
	}

	async function handleAddTcp() {
		tcpError = '';
		const entries = parseIpRange(tcpInput.trim());
		if (entries.length === 0) {
			tcpError = 'No valid IPs found';
			return;
		}

		addingTcp = true;
		try {
			if (entries.length === 1) {
				await addTcpDevice(entries[0].host, entries[0].port);
			} else {
				await addMultipleTcpDevices(entries.map(e => ({ host: e.host, port: e.port })));
			}
			tcpInput = '';
			showTcpInput = false;
		} catch (e) {
			tcpError = e instanceof Error ? e.message : String(e);
		} finally {
			addingTcp = false;
		}
	}

	async function handleScan() {
		tcpError = '';
		const entries = parseIpRange(tcpInput.trim());
		if (entries.length === 0) {
			tcpError = 'No valid IPs to scan';
			return;
		}
		await scanAndAddDevices(entries.map(e => e.host), entries[0]?.port ?? 5555);
	}

	function handleTcpKeydown(e: KeyboardEvent) {
		if (e.key === 'Enter' && !e.shiftKey) {
			e.preventDefault();
			if (isScannable) handleScan();
			else handleAddTcp();
		}
		if (e.key === 'Escape') {
			showTcpInput = false;
			tcpInput = '';
			tcpError = '';
		}
	}
</script>

<div
	class="h-full overflow-y-auto"
	role="region"
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
				{#if usbCount > 0 && tcpCount > 0}
					<span class="text-xs text-muted ml-1">({usbCount} USB, {tcpCount} TCP)</span>
				{/if}
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

	<!-- Scan progress bar (shown at top when scanning) -->
	{#if progress && (progress.type === 'start' || progress.type === 'progress')}
		<div class="px-4 py-2 bg-surface border-b border-border">
			<div class="flex items-center justify-between text-[10px] mb-1">
				<span class="text-muted">Scanning {progress.scanned ?? 0}/{progress.total ?? 0}</span>
				<div class="flex items-center gap-2">
					<span class="text-success font-bold">{progress.found ?? 0} found</span>
					<button class="text-error text-[10px]" onclick={cancelScan}>Cancel</button>
				</div>
			</div>
			<div class="h-1 bg-border rounded-full overflow-hidden">
				<div
					class="h-full rounded-full transition-all bg-primary"
					style="width: {progress.total ? ((progress.scanned ?? 0) / progress.total * 100) : 0}%"
				></div>
			</div>
		</div>
	{/if}
	{#if progress?.type === 'done'}
		<div class="px-4 py-2 bg-success/5 border-b border-success/20 flex items-center justify-between">
			<span class="text-[10px] text-success">Scan complete: {progress.found} device{progress.found !== 1 ? 's' : ''} found in {((progress.elapsedMs ?? 0) / 1000).toFixed(1)}s</span>
		</div>
	{/if}

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

	<!-- Add device section -->
	<div class="px-3 pb-4 space-y-2">
		<!-- TCP input (toggle) -->
		{#if showTcpInput && tcpProxyAvailable}
			<div class="p-3 rounded-xl bg-surface border border-border space-y-2">
				<div class="flex items-center justify-between">
					<span class="text-xs font-bold text-foreground">Add TCP/IP Devices</span>
					<button
						class="text-xs text-muted active:text-foreground"
						onclick={() => { showTcpInput = false; tcpInput = ''; tcpError = ''; }}
					>Cancel</button>
				</div>
				<input
					type="text"
					bind:value={tcpInput}
					onkeydown={handleTcpKeydown}
					placeholder="10.105.0.0/24 or 10.105.0.12-48"
					disabled={scanning}
					class="w-full px-3 py-2 text-xs rounded-lg bg-background border border-border text-foreground placeholder:text-muted/50 focus:outline-none focus:border-primary disabled:opacity-50"
				/>
				{#if tcpError}
					<div class="text-xs text-error">{tcpError}</div>
				{/if}
				{#if parsedCount > 0 && !scanning}
					<div class="text-[10px] text-muted">{describeIpInput(tcpInput)}</div>
				{/if}

				{#if scanning}
					<button
						class="w-full py-2.5 rounded-lg text-xs font-bold bg-error/10 text-error active:bg-error/20 min-h-[40px]"
						onclick={cancelScan}
					>Cancel Scan</button>
				{:else if isScannable}
					<div class="flex gap-2">
						<button
							class="flex-1 py-2.5 rounded-lg text-xs font-bold bg-primary text-background active:bg-primary-dim min-h-[40px]"
							onclick={handleScan}
						>Scan {parsedCount} IPs</button>
						<button
							class="py-2.5 px-3 rounded-lg text-xs font-bold bg-primary/10 text-primary active:bg-primary/20 min-h-[40px]"
							onclick={handleAddTcp}
							disabled={addingTcp}
							title="Connect all without scanning"
						>Force All</button>
					</div>
				{:else}
					<button
						class="w-full py-2.5 rounded-lg text-xs font-bold bg-primary text-background active:bg-primary-dim min-h-[40px]"
						onclick={handleAddTcp}
						disabled={addingTcp || !tcpInput.trim()}
					>
						{#if addingTcp}
							<div class="flex items-center justify-center gap-2">
								<div class="w-3 h-3 border border-current border-t-transparent rounded-full spinner"></div>
								Connecting...
							</div>
						{:else}
							Connect
						{/if}
					</button>
				{/if}
			</div>
		{/if}

		<!-- Action buttons -->
		<div class="flex gap-2">
			{#if tcpProxyAvailable}
				<button
					class="flex-1 py-3 rounded-xl border-2 border-dashed border-border text-xs text-muted active:border-primary active:text-primary transition-colors min-h-[48px]"
					onclick={() => { showTcpInput = !showTcpInput; }}
					disabled={addingTcp || scanning}
				>
					+ Add TCP/IP Device
				</button>
			{/if}

			{#if webUsbSupported}
				<button
					class="flex-1 py-3 rounded-xl border-2 border-dashed border-border text-xs text-muted active:border-primary active:text-primary transition-colors min-h-[48px]"
					onclick={handleAddUsb}
					disabled={addingUsb}
				>
					{#if addingUsb}
						<div class="flex items-center justify-center gap-2">
							<div class="w-3 h-3 border border-current border-t-transparent rounded-full spinner"></div>
							Select USB devices...
						</div>
					{:else}
						+ Add USB Device
					{/if}
				</button>
			{/if}
		</div>
	</div>
</div>
