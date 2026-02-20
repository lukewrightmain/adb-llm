<script lang="ts">
	import type { Device } from '$lib/types';

	interface Props {
		device: Device;
		rank?: number | null;
		layers?: number | null;
		running?: boolean;
		selected?: boolean;
		selectable?: boolean;
		onToggle?: (serial: string) => void;
	}

	let {
		device,
		rank = null,
		layers = null,
		running = false,
		selected = false,
		selectable = false,
		onToggle,
	}: Props = $props();

	function thermalColor(temp: number): string {
		if (temp <= 35) return '#60a5fa';
		if (temp <= 42) return '#3fb950';
		if (temp <= 50) return '#d29922';
		return '#f85149';
	}

	function ramPercent(dev: Device): number {
		if (!dev.total_ram_mb) return 0;
		return Math.round(((dev.total_ram_mb - dev.available_ram_mb) / dev.total_ram_mb) * 100);
	}

	const stateColor: Record<string, string> = {
		ready: 'bg-success',
		connected: 'bg-primary',
		busy: 'bg-warning',
		error: 'bg-error',
		offline: 'bg-muted',
	};

	function handleClick() {
		if (selectable && onToggle) onToggle(device.serial);
	}
</script>

<!-- svelte-ignore a11y_click_events_have_key_events -->
<!-- svelte-ignore a11y_no_static_element_interactions -->
<div
	class="border rounded-lg p-3 bg-surface transition-colors text-xs
		{selectable ? 'cursor-pointer' : ''}
		{selected ? 'border-primary bg-primary/5 ring-1 ring-primary/30' : 'border-border hover:bg-surface-hover'}"
	onclick={handleClick}
>
	<div class="flex items-center justify-between mb-2">
		<div class="flex items-center gap-2">
			{#if selectable}
				<span class="w-3.5 h-3.5 rounded border flex items-center justify-center shrink-0
					{selected ? 'border-primary bg-primary' : 'border-muted'}">
					{#if selected}
						<svg class="w-2.5 h-2.5 text-background" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="2">
							<path d="M2 6l3 3 5-5" />
						</svg>
					{/if}
				</span>
			{/if}
			<span class="w-2 h-2 rounded-full {stateColor[device.state] ?? 'bg-muted'}"></span>
			<span class="font-bold text-foreground">{device.short_serial}</span>
			{#if rank !== null}
				<span class="text-primary text-[10px] bg-primary/10 px-1.5 py-0.5 rounded">R{rank}</span>
			{/if}
		</div>
		{#if running}
			<span class="text-success text-[10px]">ACTIVE</span>
		{/if}
	</div>

	<div class="text-muted mb-2">{device.model}</div>

	<!-- RAM bar -->
	<div class="mb-1.5">
		<div class="flex justify-between text-[10px] text-muted mb-0.5">
			<span>RAM</span>
			<span>{device.available_ram_mb}MB / {device.total_ram_mb}MB</span>
		</div>
		<div class="w-full h-1.5 bg-border rounded-full overflow-hidden">
			<div
				class="h-full rounded-full transition-all"
				style="width: {ramPercent(device)}%; background: {thermalColor(device.thermal_temp_c)}"
			></div>
		</div>
	</div>

	<!-- Stats row -->
	<div class="flex justify-between text-[10px] text-muted">
		<span style="color: {thermalColor(device.thermal_temp_c)}">{device.thermal_temp_c.toFixed(1)}&deg;C</span>
		{#if layers !== null}
			<span>{layers} layers</span>
		{/if}
		<span>{device.models.length} models</span>
	</div>
</div>
