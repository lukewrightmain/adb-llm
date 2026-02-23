<script lang="ts">
	let { message, type = 'info', onclose }: { message: string; type?: 'info' | 'success' | 'error' | 'warning'; onclose?: () => void } = $props();

	function bgClass(t: string): string {
		switch (t) {
			case 'success': return 'bg-success/15 border-success/30 text-success';
			case 'error': return 'bg-error/15 border-error/30 text-error';
			case 'warning': return 'bg-warning/15 border-warning/30 text-warning';
			default: return 'bg-primary/15 border-primary/30 text-primary';
		}
	}
</script>

<div class="fixed bottom-20 left-1/2 -translate-x-1/2 z-50 px-4 py-2.5 rounded-xl border text-xs font-bold shadow-lg {bgClass(type)} slide-in">
	<div class="flex items-center gap-2">
		<span>{message}</span>
		{#if onclose}
			<button class="opacity-60 hover:opacity-100" onclick={onclose}>
				<svg class="w-3.5 h-3.5" viewBox="0 0 20 20" fill="currentColor">
					<path fill-rule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clip-rule="evenodd"/>
				</svg>
			</button>
		{/if}
	</div>
</div>
