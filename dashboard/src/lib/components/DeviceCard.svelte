<script lang="ts">
	import type { ManagedDevice } from '$lib/types/adb';
	import PhoneSilhouette from './shared/PhoneSilhouette.svelte';

	let {
		device,
		selected = false,
		onclick,
	}: {
		device: ManagedDevice;
		selected?: boolean;
		onclick?: () => void;
	} = $props();

	const info = $derived(device.info);
	const ramPercent = $derived(
		info && info.totalRamMb > 0
			? Math.round((info.availableRamMb / info.totalRamMb) * 100)
			: 0
	);

	function thermalColor(temp: number): string {
		if (temp > 42) return 'text-error';
		if (temp > 38) return 'text-warning';
		return 'text-muted';
	}
</script>

<button
	class="w-full text-left p-3 rounded-xl border transition-all group
		{selected ? 'border-primary bg-primary/5 ring-1 ring-primary/20' : 'border-border bg-surface'}
		active:bg-surface-hover min-h-[80px]"
	{onclick}
>
	<div class="flex gap-3">
		<!-- Phone silhouette -->
		<PhoneSilhouette
			state={device.state}
			{ramPercent}
			compact
			id={device.serial}
		/>

		<!-- Info -->
		<div class="flex-1 min-w-0">
			<div class="flex items-center gap-2 mb-1">
				<!-- Status dot -->
				<div class="w-2 h-2 rounded-full shrink-0 {
					device.state === 'ready' ? 'bg-success' :
					device.state === 'busy' ? 'bg-warning status-pulse' :
					device.state === 'error' ? 'bg-error' :
					device.state === 'connecting' || device.state === 'probing' ? 'bg-primary status-pulse' :
					'bg-muted'
				}"></div>

				<!-- Serial -->
				<span class="text-xs font-bold truncate">{device.shortSerial}</span>

				<!-- Checkmark -->
				{#if selected}
					<svg class="w-4 h-4 text-primary shrink-0 ml-auto" viewBox="0 0 20 20" fill="currentColor">
						<path fill-rule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clip-rule="evenodd"/>
					</svg>
				{/if}
			</div>

			{#if info}
				<!-- Model name -->
				<div class="text-[10px] text-muted truncate mb-1">{info.model}</div>

				<!-- Info row -->
				<div class="flex items-center gap-2.5 text-[10px]">
					<!-- RAM -->
					<div class="flex items-center gap-1 flex-1 min-w-0">
						<span class="text-muted shrink-0">RAM</span>
						<div class="flex-1 h-1.5 bg-border rounded-full overflow-hidden">
							<div
								class="h-full rounded-full transition-all {ramPercent > 50 ? 'bg-primary' : ramPercent > 25 ? 'bg-warning' : 'bg-error'}"
								style="width: {ramPercent}%"
							></div>
						</div>
						<span class="text-muted shrink-0">{ramPercent}%</span>
					</div>

					<!-- Thermal -->
					{#if info.thermalTempC > 0}
						<span class="{thermalColor(info.thermalTempC)} shrink-0">{info.thermalTempC}°</span>
					{/if}

					<!-- Ring rank -->
					{#if device.ringRank !== null}
						<span class="px-1.5 py-0.5 rounded text-[9px] bg-primary/15 text-primary shrink-0">
							R{device.ringRank}
						</span>
					{/if}

					<!-- Model count -->
					{#if device.models.length > 0}
						<span class="text-muted shrink-0">{device.models.length}M</span>
					{/if}
				</div>
			{:else if device.state === 'connecting' || device.state === 'probing'}
				<div class="flex items-center gap-2 text-[10px] text-muted">
					<div class="w-3 h-3 border border-current border-t-transparent rounded-full spinner"></div>
					{device.state === 'connecting' ? 'Connecting...' : 'Probing...'}
				</div>
			{:else if device.state === 'error'}
				<div class="text-[10px] text-error truncate">{device.lastError ?? 'Connection error'}</div>
			{:else}
				<div class="text-[10px] text-muted">Disconnected</div>
			{/if}
		</div>
	</div>
</button>
