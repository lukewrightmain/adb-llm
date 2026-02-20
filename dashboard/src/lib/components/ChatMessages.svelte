<script lang="ts">
	import { getActiveConversation, isStreaming } from '$lib/stores/app.svelte';
	import type { ChatMessage } from '$lib/types';
	import { tick } from 'svelte';

	let scrollRef: HTMLDivElement | undefined = $state();
	const conv = $derived(getActiveConversation());
	const messages = $derived(conv?.messages ?? []);
	const busy = $derived(isStreaming());

	// Auto-scroll on new messages
	$effect(() => {
		messages;
		tick().then(() => {
			if (scrollRef) scrollRef.scrollTop = scrollRef.scrollHeight;
		});
	});

	function formatTps(msg: ChatMessage): string {
		if (!msg.tps) return '';
		return `${msg.tps.toFixed(1)} tok/s`;
	}

	function formatTtft(msg: ChatMessage): string {
		if (!msg.ttftMs) return '';
		return `TTFT: ${msg.ttftMs}ms`;
	}
</script>

<div bind:this={scrollRef} class="flex-1 overflow-y-auto scrollbar-hide p-4 space-y-4">
	{#if messages.length === 0}
		<div class="flex items-center justify-center h-full text-muted text-sm">
			Start a conversation with the cellswarm ring
		</div>
	{:else}
		{#each messages as msg (msg.id)}
			<div class="flex {msg.role === 'user' ? 'justify-end' : 'justify-start'}">
				<div
					class="max-w-[85%] rounded-lg px-3 py-2 text-sm {msg.role === 'user'
						? 'bg-primary/20 text-foreground'
						: 'bg-surface border border-border text-foreground'}"
				>
					<div class="whitespace-pre-wrap break-words">{msg.content}{#if msg.role === 'assistant' && busy && !msg.content}<span class="inline-block w-2 h-4 bg-primary/60 animate-pulse ml-0.5"></span>{/if}</div>
					{#if msg.role === 'assistant' && (msg.tps || msg.ttftMs)}
						<div class="mt-1 text-[10px] text-muted flex gap-2">
							{#if msg.ttftMs}<span>{formatTtft(msg)}</span>{/if}
							{#if msg.tps}<span>{formatTps(msg)}</span>{/if}
						</div>
					{/if}
				</div>
			</div>
		{/each}
	{/if}
</div>
