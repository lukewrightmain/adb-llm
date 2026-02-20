<script lang="ts">
	import { sendMessage, isStreaming } from '$lib/stores/app.svelte';

	let input = $state('');
	let textarea: HTMLTextAreaElement | undefined = $state();
	const busy = $derived(isStreaming());

	async function handleSubmit() {
		const text = input.trim();
		if (!text || busy) return;
		input = '';
		if (textarea) textarea.style.height = 'auto';
		await sendMessage(text);
	}

	function handleKeydown(e: KeyboardEvent) {
		if (e.key === 'Enter' && !e.shiftKey) {
			e.preventDefault();
			handleSubmit();
		}
	}

	function autoResize() {
		if (!textarea) return;
		textarea.style.height = 'auto';
		textarea.style.height = Math.min(textarea.scrollHeight, 120) + 'px';
	}
</script>

<form class="flex gap-2 items-end" onsubmit={(e) => { e.preventDefault(); handleSubmit(); }}>
	<textarea
		bind:this={textarea}
		bind:value={input}
		onkeydown={handleKeydown}
		oninput={autoResize}
		placeholder="Type a message..."
		rows={1}
		class="flex-1 bg-background border border-border rounded-lg px-3 py-2 text-sm text-foreground resize-none focus:outline-none focus:border-primary/50 scrollbar-hide"
		disabled={busy}
	></textarea>
	<button
		type="submit"
		class="px-4 py-2 rounded-lg text-sm font-bold transition-colors disabled:opacity-30
			{busy ? 'bg-muted text-background' : 'bg-primary text-background hover:bg-primary-dim'}"
		disabled={busy || !input.trim()}
	>
		{busy ? '...' : 'Send'}
	</button>
</form>
