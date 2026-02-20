<script lang="ts">
	import '../app.css';

	let { children } = $props();
</script>

<svelte:head>
	<title>CellSwarm Dashboard</title>
	<meta name="description" content="CellSwarm Phone Ring Cluster Dashboard" />
</svelte:head>

<div class="min-h-screen bg-background text-foreground">
	{@render children?.()}
</div>
