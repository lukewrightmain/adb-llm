<script lang="ts">
	import '../app.css';

	let { children } = $props();
</script>

<svelte:head>
	<title>CellSwarm</title>
	<meta name="description" content="CellSwarm Phone Ring Cluster Dashboard" />
	<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover, maximum-scale=1, user-scalable=no" />
	<meta name="apple-mobile-web-app-capable" content="yes" />
	<meta name="mobile-web-app-capable" content="yes" />
</svelte:head>

<div class="h-dvh flex flex-col bg-background text-foreground overflow-hidden">
	{@render children?.()}
</div>
