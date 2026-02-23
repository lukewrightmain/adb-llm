<script lang="ts">
	import SetupWizard from '$lib/components/SetupWizard.svelte';
	import DevicesPanel from '$lib/components/DevicesPanel.svelte';
	import RingPanel from '$lib/components/RingPanel.svelte';
	import ChatPanel from '$lib/components/ChatPanel.svelte';
	import ModelsPanel from '$lib/components/ModelsPanel.svelte';
	import SettingsPanel from '$lib/components/SettingsPanel.svelte';
	import {
		getActiveTab,
		setActiveTab,
		isRingActive,
		getRingHealth,
		getManagedDevices,
		getGlobalError,
		getWebUsbSupported,
		dismissError,
		initWebUsb,
		startPolling,
		stopPolling,
		destroy,
	} from '$lib/stores/app.svelte';
	import { onMount, onDestroy } from 'svelte';
	import type { TabId } from '$lib/types';

	const tab = $derived(getActiveTab());
	const ringActive = $derived(isRingActive());
	const health = $derived(getRingHealth());
	const devices = $derived(getManagedDevices());
	const globalError = $derived(getGlobalError());
	const webUsbSupported = $derived(getWebUsbSupported());

	const readyCount = $derived(devices.filter(d => d.state === 'ready').length);
	const hasDevices = $derived(devices.length > 0);

	const tabs: { id: TabId; label: string; icon: string }[] = [
		{ id: 'devices', label: 'Devices', icon: 'M12 18h.01M8 21h8a2 2 0 002-2V5a2 2 0 00-2-2H8a2 2 0 00-2 2v14a2 2 0 002 2z' },
		{ id: 'ring', label: 'Ring', icon: 'M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z' },
		{ id: 'chat', label: 'Chat', icon: 'M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z' },
		{ id: 'models', label: 'Models', icon: 'M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4M4 7c0-2.21 3.582-4 8-4s8 1.79 8 4m0 5c0 2.21-3.582 4-8 4s-8-1.79-8-4' },
		{ id: 'settings', label: 'Settings', icon: 'M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z M15 12a3 3 0 11-6 0 3 3 0 016 0z' },
	];

	onMount(async () => {
		await initWebUsb();
		startPolling();
	});

	onDestroy(() => {
		stopPolling();
		destroy();
	});
</script>

<!-- Show setup wizard if no devices connected and WebUSB is available -->
{#if !hasDevices && webUsbSupported}
	<SetupWizard />
{:else if !webUsbSupported}
	<SetupWizard />
{:else}
	<!-- Global error banner -->
	{#if globalError}
		<div class="shrink-0 flex items-center gap-2 px-3 py-2 bg-error/10 border-b border-error/20">
			<span class="flex-1 text-xs text-error">{globalError}</span>
			<button
				class="w-8 h-8 flex items-center justify-center rounded text-error active:text-error/60"
				onclick={dismissError}
				aria-label="Dismiss error"
			>
				<svg class="w-4 h-4" viewBox="0 0 20 20" fill="currentColor">
					<path fill-rule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clip-rule="evenodd"/>
				</svg>
			</button>
		</div>
	{/if}

	<!-- Main content area -->
	<main class="flex-1 overflow-hidden">
		{#if tab === 'devices'}
			<DevicesPanel />
		{:else if tab === 'ring'}
			<RingPanel />
		{:else if tab === 'chat'}
			<ChatPanel />
		{:else if tab === 'models'}
			<ModelsPanel />
		{:else if tab === 'settings'}
			<SettingsPanel />
		{/if}
	</main>

	<!-- Bottom tab bar -->
	<nav class="shrink-0 border-t border-border bg-background safe-bottom">
		<div class="flex h-14">
			{#each tabs as t (t.id)}
				<button
					class="flex-1 flex flex-col items-center justify-center gap-0.5 relative transition-colors min-h-[56px]
						{tab === t.id ? 'text-primary' : 'text-muted active:text-foreground'}"
					onclick={() => setActiveTab(t.id)}
				>
					<div class="relative">
						<svg class="w-5 h-5" fill="none" stroke="currentColor" stroke-width="1.5" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" d={t.icon} />
						</svg>

						<!-- Ring active badge -->
						{#if t.id === 'ring' && ringActive}
							<div class="absolute -top-0.5 -right-0.5 w-2 h-2 rounded-full {health.ready ? 'bg-success' : 'bg-warning status-pulse'}"></div>
						{/if}
					</div>

					<span class="text-[9px] font-bold leading-none">
						{t.label}
						{#if t.id === 'devices'}
							<span class="text-muted font-normal">({readyCount})</span>
						{/if}
					</span>
				</button>
			{/each}
		</div>
	</nav>
{/if}
