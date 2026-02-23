<script lang="ts">
	import type { DeviceState } from '$lib/types/adb';

	let { state }: { state: DeviceState } = $props();

	function color(s: DeviceState): string {
		switch (s) {
			case 'ready': return 'bg-success';
			case 'busy': return 'bg-warning';
			case 'error': return 'bg-error';
			case 'connected': case 'probing': return 'bg-primary';
			case 'connecting': return 'bg-primary';
			default: return 'bg-muted';
		}
	}

	function label(s: DeviceState): string {
		switch (s) {
			case 'disconnected': return 'Offline';
			case 'connecting': return 'Connecting';
			case 'connected': return 'Connected';
			case 'probing': return 'Probing';
			case 'ready': return 'Ready';
			case 'busy': return 'Busy';
			case 'error': return 'Error';
			default: return s;
		}
	}

	const pulse = $derived(state === 'connecting' || state === 'probing' || state === 'busy');
</script>

<span class="inline-flex items-center gap-1.5">
	<span class="w-2 h-2 rounded-full shrink-0 {color(state)} {pulse ? 'status-pulse' : ''}"></span>
	<span class="text-[10px] text-muted">{label(state)}</span>
</span>
