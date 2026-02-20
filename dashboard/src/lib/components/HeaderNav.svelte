<script lang="ts">
	import { getActiveTab, setActiveTab, getRingStatus, getReadyCount } from '$lib/stores/app.svelte';

	const tab = $derived(getActiveTab());
	const ring = $derived(getRingStatus());
	const ready = $derived(getReadyCount());

	const tabs = [
		{ id: 'topology' as const, label: 'Topology' },
		{ id: 'chat' as const, label: 'Chat' },
		{ id: 'models' as const, label: 'Models' },
	];
</script>

<header class="border-b border-border bg-surface px-4 py-3 flex items-center justify-between">
	<div class="flex items-center gap-3">
		<h1 class="text-primary font-bold text-lg tracking-wide">CELLSWARM</h1>

		<!-- Ring status indicator -->
		<div class="flex items-center gap-1.5 text-xs">
			{#if ring.active}
				<span class="w-2 h-2 rounded-full bg-success status-pulse"></span>
				<span class="text-success">{ring.world_size} nodes</span>
			{:else}
				<span class="w-2 h-2 rounded-full bg-muted"></span>
				<span class="text-muted">offline</span>
			{/if}
			<span class="text-border">|</span>
			<span class="text-muted">{ready} phones</span>
		</div>
	</div>

	<nav class="flex gap-1">
		{#each tabs as t}
			<button
				class="px-3 py-1.5 rounded text-sm transition-colors {tab === t.id
					? 'bg-primary/20 text-primary'
					: 'text-muted hover:text-foreground hover:bg-surface-hover'}"
				onclick={() => setActiveTab(t.id)}
			>
				{t.label}
			</button>
		{/each}
	</nav>
</header>
