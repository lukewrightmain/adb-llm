<script lang="ts">
	import {
		getConversations,
		getActiveConversationId,
		getActiveConversation,
		isRingActive,
		getRingHealth,
		isStreaming,
		getStreamingContent,
		newConversation,
		setActiveConversation,
		deleteConversation,
		sendMessage,
		stopStreaming,
		setActiveTab,
	} from '$lib/stores/app.svelte';
	import { renderMarkdown } from '$lib/utils/markdown';
	import { tick } from 'svelte';

	const conversations = $derived(getConversations());
	const activeConvId = $derived(getActiveConversationId());
	const activeConv = $derived(getActiveConversation());
	const ringActive = $derived(isRingActive());
	const health = $derived(getRingHealth());
	const streaming = $derived(isStreaming());
	const streamingContent = $derived(getStreamingContent());

	let input = $state('');
	let showDrawer = $state(false);
	let messagesEl: HTMLDivElement | undefined = $state();
	let textareaEl: HTMLTextAreaElement | undefined = $state();

	// Auto-scroll on new messages or streaming content
	$effect(() => {
		activeConv?.messages.length;
		streamingContent;
		tick().then(() => {
			if (messagesEl) messagesEl.scrollTop = messagesEl.scrollHeight;
		});
	});

	function resizeTextarea() {
		if (!textareaEl) return;
		textareaEl.style.height = 'auto';
		textareaEl.style.height = Math.min(textareaEl.scrollHeight, 120) + 'px';
	}

	async function handleSend() {
		const text = input.trim();
		if (!text || streaming) return;
		input = '';
		if (textareaEl) { textareaEl.style.height = 'auto'; }
		await sendMessage(text);
	}

	function handleKeydown(e: KeyboardEvent) {
		if (e.key === 'Enter' && !e.shiftKey) {
			e.preventDefault();
			handleSend();
		}
	}

	function handleNewChat() {
		newConversation();
		showDrawer = false;
	}

	function handleSelectConv(id: string) {
		setActiveConversation(id);
		showDrawer = false;
	}

	function formatTime(ts: number): string {
		return new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
	}
</script>

<div class="h-full flex flex-col relative">
	<!-- Header -->
	<div class="shrink-0 flex items-center gap-2 px-3 py-2 border-b border-border bg-background/95 backdrop-blur min-h-[48px]">
		<button
			class="w-10 h-10 flex items-center justify-center rounded-lg active:bg-surface-hover"
			onclick={() => showDrawer = !showDrawer}
		>
			<svg class="w-5 h-5 text-muted" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
				<path d="M4 6h16M4 12h16M4 18h16"/>
			</svg>
		</button>

		<span class="flex-1 text-xs font-bold truncate">
			{activeConv?.name ?? 'No conversation'}
		</span>

		{#if streaming}
			<button
				class="px-3 py-1.5 text-xs rounded-lg bg-error/10 text-error active:bg-error/20 min-h-[36px]"
				onclick={stopStreaming}
			>Stop</button>
		{:else}
			<button
				class="px-3 py-1.5 text-xs rounded-lg bg-primary/10 text-primary active:bg-primary/20 min-h-[36px]"
				onclick={handleNewChat}
			>+ New</button>
		{/if}
	</div>

	<!-- Status banner -->
	{#if !ringActive}
		<button
			class="w-full px-3 py-2.5 bg-error/10 text-error text-[10px] border-b border-error/20 text-left active:bg-error/15 min-h-[44px]"
			onclick={() => setActiveTab('ring')}
		>
			Ring not active. Tap here to start one.
		</button>
	{:else if !health.ready}
		<div class="px-3 py-2 bg-warning/10 text-warning text-[10px] border-b border-warning/20 flex items-center gap-2">
			<div class="w-3 h-3 border-2 border-warning border-t-transparent rounded-full spinner shrink-0"></div>
			Model loading... you can send messages once ready.
		</div>
	{/if}

	<!-- Messages -->
	<div class="flex-1 overflow-y-auto px-3 py-2 space-y-3" bind:this={messagesEl}>
		{#if !activeConv || activeConv.messages.length === 0}
			<div class="flex items-center justify-center h-full text-muted text-xs">
				{#if !activeConv}
					Tap "+ New" to start a conversation
				{:else}
					Send a message to begin
				{/if}
			</div>
		{:else}
			{#each activeConv.messages as msg (msg.id)}
				{#if msg.role === 'user'}
					<div class="flex justify-end">
						<div class="max-w-[85%] px-3 py-2 rounded-2xl rounded-br-sm bg-primary text-background text-xs leading-relaxed break-words">
							{msg.content}
						</div>
					</div>
				{:else if msg.role === 'assistant'}
					{@const isLastMsg = msg === activeConv.messages[activeConv.messages.length - 1]}
					{@const displayContent = (streaming && isLastMsg) ? streamingContent : msg.content}
					<div class="flex justify-start">
						<div class="max-w-[85%]">
							<div class="px-3 py-2 rounded-2xl rounded-bl-sm bg-surface border border-border text-xs leading-relaxed break-words md-content">
								{#if displayContent}
									{@html renderMarkdown(displayContent)}
								{/if}
								{#if streaming && isLastMsg}
									<span class="cursor-blink text-primary">&#9612;</span>
								{/if}
							</div>
							{#if msg.ttftMs !== undefined && !streaming}
								<div class="text-[9px] text-muted mt-0.5 px-1">
									TTFT {msg.ttftMs}ms &middot; {msg.tps?.toFixed(1)} tok/s
								</div>
							{/if}
						</div>
					</div>
				{/if}
			{/each}
		{/if}
	</div>

	<!-- Input bar -->
	<div class="shrink-0 border-t border-border bg-background p-2">
		<div class="flex items-end gap-2">
			<textarea
				bind:this={textareaEl}
				bind:value={input}
				oninput={resizeTextarea}
				onkeydown={handleKeydown}
				placeholder={ringActive && health.ready ? 'Message...' : 'Start a ring first...'}
				rows={1}
				disabled={!ringActive || !health.ready}
				class="flex-1 bg-surface border border-border rounded-xl px-3 py-2.5 text-xs text-foreground resize-none min-h-[44px] max-h-[120px] outline-none focus:border-primary/50 disabled:opacity-50"
			></textarea>
			<button
				class="w-11 h-11 flex items-center justify-center rounded-xl transition-colors shrink-0
					{input.trim() && !streaming && ringActive && health.ready ? 'bg-primary text-background active:bg-primary-dim' : 'bg-surface text-muted'}"
				onclick={handleSend}
				disabled={!input.trim() || streaming || !ringActive || !health.ready}
			>
				<svg class="w-5 h-5" viewBox="0 0 24 24" fill="currentColor">
					<path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/>
				</svg>
			</button>
		</div>
	</div>

	<!-- Conversation drawer overlay -->
	{#if showDrawer}
		<button
			class="absolute inset-0 bg-black/50 z-20"
			onclick={() => showDrawer = false}
			aria-label="Close drawer"
		></button>

		<div class="absolute top-0 left-0 bottom-0 w-64 bg-background border-r border-border z-30 slide-in flex flex-col">
			<div class="p-3 border-b border-border">
				<h3 class="text-xs font-bold">Conversations</h3>
			</div>
			<div class="flex-1 overflow-y-auto p-2 space-y-1">
				{#each conversations as conv (conv.id)}
					<div class="flex items-center gap-1">
						<button
							class="flex-1 text-left px-2.5 py-2 rounded-lg text-xs truncate min-h-[44px] flex items-center
								{conv.id === activeConvId ? 'bg-surface-hover text-foreground' : 'text-muted active:bg-surface-hover'}"
							onclick={() => handleSelectConv(conv.id)}
						>
							{conv.name}
						</button>
						<button
							class="w-8 h-8 flex items-center justify-center rounded text-muted active:text-error shrink-0"
							onclick={() => deleteConversation(conv.id)}
						>
							<svg class="w-3.5 h-3.5" viewBox="0 0 20 20" fill="currentColor">
								<path fill-rule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clip-rule="evenodd"/>
							</svg>
						</button>
					</div>
				{/each}

				{#if conversations.length === 0}
					<div class="text-center text-muted text-[10px] py-4">No conversations yet</div>
				{/if}
			</div>
		</div>
	{/if}
</div>
