<script lang="ts">
	let { progress = 0, label = '' }: { progress?: number; label?: string } = $props();
	const pct = $derived(Math.round(progress * 100));
</script>

<div>
	{#if label}
		<div class="flex justify-between text-[10px] text-muted mb-1">
			<span>{label}</span>
			<span>{pct}%</span>
		</div>
	{/if}
	<div class="w-full h-1.5 bg-border rounded-full overflow-hidden">
		<div
			class="h-full bg-primary rounded-full transition-all duration-300"
			style="width: {pct}%"
		></div>
	</div>
</div>
