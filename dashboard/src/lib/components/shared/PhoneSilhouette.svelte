<script lang="ts">
	import type { DeviceState } from '$lib/types/adb';

	let {
		state = 'disconnected',
		ramPercent = 0,
		compact = false,
		id = 'default',
	}: {
		state?: DeviceState;
		ramPercent?: number;
		compact?: boolean;
		id?: string;
	} = $props();

	function statusColor(s: DeviceState): string {
		switch (s) {
			case 'ready': return '#3fb950';
			case 'busy': return '#d29922';
			case 'error': return '#f85149';
			case 'connected': case 'probing': case 'connecting': return '#3b82f6';
			default: return '#8b949e';
		}
	}

	function ramColor(pct: number): string {
		if (pct > 50) return '#3b82f6';
		if (pct > 25) return '#d29922';
		return '#f85149';
	}

	const w = $derived(compact ? 32 : 48);
	const h = $derived(compact ? 56 : 80);
	const rx = $derived(compact ? 4 : 6);
	const ramH = $derived(Math.round((h - 16) * (ramPercent / 100)));
</script>

<svg width={w} height={h} viewBox="0 0 {w} {h}" class="shrink-0">
	<!-- Phone outline -->
	<rect x="1" y="1" width={w-2} height={h-2} rx={rx} ry={rx}
		fill="var(--color-surface)" stroke="var(--color-border)" stroke-width="1.5" />

	<!-- RAM fill from bottom -->
	{#if ramPercent > 0}
		<clipPath id="phone-clip-{id}">
			<rect x="3" y="8" width={w-6} height={h-16} rx={rx-2} ry={rx-2} />
		</clipPath>
		<rect
			x="3" y={8 + (h - 16) - ramH} width={w-6} height={ramH}
			fill={ramColor(ramPercent)} opacity="0.3"
			clip-path="url(#phone-clip-{id})"
		/>
	{/if}

	<!-- Status dot -->
	<circle cx={w/2} cy={h - 5} r="2.5" fill={statusColor(state)} />

	<!-- Camera notch -->
	<circle cx={w/2} cy="5" r="1.5" fill="var(--color-border)" />
</svg>
