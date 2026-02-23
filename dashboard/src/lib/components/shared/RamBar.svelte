<script lang="ts">
	let { available, total, height = 'h-24' }: { available: number; total: number; height?: string } = $props();

	const percent = $derived(total > 0 ? Math.round((available / total) * 100) : 0);

	function fillColor(pct: number): string {
		if (pct > 50) return 'bg-primary';
		if (pct > 25) return 'bg-warning';
		return 'bg-error';
	}
</script>

<!-- Battery-style vertical RAM bar -->
<div class="relative w-full {height} rounded-md border border-border/50 overflow-hidden bg-surface/50">
	<div
		class="absolute bottom-0 left-0 right-0 rounded-b-sm transition-all duration-500 {fillColor(percent)}"
		style="height: {percent}%"
	></div>
	<div class="absolute inset-0 flex items-center justify-center">
		<span class="text-[9px] font-bold text-foreground/80 drop-shadow-sm">
			{available > 1024 ? `${(available / 1024).toFixed(1)}G` : `${available}M`}
		</span>
	</div>
</div>
