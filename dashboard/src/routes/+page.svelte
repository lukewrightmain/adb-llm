<script lang="ts">
	import {
		HeaderNav,
		RingTopology,
		DeviceCard,
		RingControls,
		ChatForm,
		ChatMessages,
		ModelManager,
	} from '$lib/components';
	import {
		getActiveTab,
		getDevices,
		getRingStatus,
		getReadyCount,
		startPolling,
		stopPolling,
		refreshDevices,
		getConversations,
		getActiveConversationId,
		newConversation,
		setActiveConversation,
		deleteConversation,
		getSelectedSerials,
		toggleDeviceSelection,
	} from '$lib/stores/app.svelte';
	import { onMount, onDestroy } from 'svelte';
	import type { RingNode, Device } from '$lib/types';

	const tab = $derived(getActiveTab());
	const allDevices = $derived(getDevices());
	const ring = $derived(getRingStatus());
	const readyCount = $derived(getReadyCount());
	const conversations = $derived(getConversations());
	const activeConvId = $derived(getActiveConversationId());
	const selectedSerials = $derived(getSelectedSerials());

	function getDeviceForNode(node: RingNode): Device | undefined {
		if (node.is_host) return undefined;
		return allDevices.find((d) => d.serial === node.serial);
	}

	onMount(() => {
		startPolling();
	});

	onDestroy(() => {
		stopPolling();
	});
</script>

<div class="h-screen flex flex-col">
	<HeaderNav />

	<main class="flex-1 overflow-hidden">
		{#if tab === 'topology'}
			<!-- Topology view -->
			<div class="h-full flex flex-col lg:flex-row">
				<!-- Ring visualization -->
				<div class="flex-1 min-h-[300px] relative grid-bg">
					<RingTopology />

					<!-- Overlay: ring info -->
					{#if ring.active}
						<div class="absolute top-3 left-3 bg-surface/90 backdrop-blur border border-border rounded-lg px-3 py-2 text-xs">
							<div class="text-primary font-bold mb-1">Ring Active</div>
							<div class="text-muted">{ring.world_size} nodes &middot; {ring.model ?? 'unknown'}</div>
						</div>
					{/if}

					<!-- Refresh button -->
					<button
						class="absolute top-3 right-3 px-2 py-1 rounded bg-surface border border-border text-xs text-muted hover:text-foreground hover:bg-surface-hover transition-colors"
						onclick={refreshDevices}
					>
						Refresh
					</button>
				</div>

				<!-- Side panel: devices + controls -->
				<div class="w-full lg:w-80 border-t lg:border-t-0 lg:border-l border-border overflow-y-auto p-3 space-y-3">
					<RingControls />

					<div>
						<h3 class="text-sm font-bold text-foreground mb-2">
							Devices <span class="text-muted font-normal">({readyCount} ready)</span>
						</h3>
						<div class="space-y-2">
							{#each allDevices as dev (dev.serial)}
								{@const ringNode = ring.nodes.find((n) => n.serial === dev.serial)}
								<DeviceCard
									device={dev}
									rank={ringNode?.rank ?? null}
									layers={ringNode?.layers ?? null}
									running={ringNode?.running ?? false}
									selectable={true}
									selected={selectedSerials.has(dev.serial)}
									onToggle={toggleDeviceSelection}
								/>
							{/each}
							{#if allDevices.length === 0}
								<div class="text-xs text-muted p-3 text-center">No devices discovered</div>
							{/if}
						</div>
					</div>
				</div>
			</div>

		{:else if tab === 'chat'}
			<!-- Chat view -->
			<div class="h-full flex">
				<!-- Conversation sidebar -->
				<div class="w-56 border-r border-border overflow-y-auto p-2 space-y-1 hidden md:block">
					<button
						class="w-full px-2 py-1.5 rounded text-xs text-primary bg-primary/10 hover:bg-primary/20 transition-colors mb-2"
						onclick={() => newConversation()}
					>
						+ New Chat
					</button>
					{#each conversations as conv (conv.id)}
						<button
							class="w-full text-left px-2 py-1.5 rounded text-xs truncate transition-colors
								{conv.id === activeConvId
									? 'bg-surface-hover text-foreground'
									: 'text-muted hover:text-foreground hover:bg-surface-hover'}"
							onclick={() => setActiveConversation(conv.id)}
						>
							{conv.name}
						</button>
					{/each}
				</div>

				<!-- Chat area -->
				<div class="flex-1 flex flex-col">
					<!-- Compact topology bar when chat is active and ring is running -->
					{#if ring.active}
						<div class="h-16 border-b border-border bg-surface shrink-0">
							<RingTopology compact={true} />
						</div>
					{/if}

					<ChatMessages />

					<div class="border-t border-border p-3">
						<ChatForm />
					</div>
				</div>
			</div>

		{:else if tab === 'models'}
			<!-- Models view -->
			<div class="h-full overflow-y-auto p-4 max-w-3xl mx-auto">
				<ModelManager />
			</div>
		{/if}
	</main>
</div>
