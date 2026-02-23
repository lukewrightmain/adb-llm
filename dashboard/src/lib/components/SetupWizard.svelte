<script lang="ts">
	import { getWebUsbSupported, addDevice, getManagedDevices } from '$lib/stores/app.svelte';

	const webUsbSupported = $derived(getWebUsbSupported());
	const devices = $derived(getManagedDevices());
	const hasDevices = $derived(devices.length > 0);

	let connecting = $state(false);

	async function handleConnect() {
		connecting = true;
		try {
			await addDevice();
		} catch (e) {
			console.error('Connect failed:', e);
		} finally {
			connecting = false;
		}
	}
</script>

<div class="h-full flex items-center justify-center p-6">
	<div class="max-w-sm w-full space-y-6 text-center">
		<!-- Logo -->
		<div class="space-y-2">
			<div class="text-4xl font-bold text-primary">CellSwarm</div>
			<div class="text-xs text-muted">Phone-Cluster LLM Inference</div>
		</div>

		{#if !webUsbSupported}
			<!-- Browser not supported -->
			<div class="p-4 rounded-xl bg-error/10 border border-error/20 text-left space-y-3">
				<div class="text-sm font-bold text-error">WebUSB Not Available</div>
				<p class="text-xs text-muted leading-relaxed">
					This dashboard requires WebUSB to communicate with phones directly. Please use:
				</p>
				<ul class="text-xs text-muted space-y-1">
					<li class="flex items-center gap-2">
						<span class="w-1.5 h-1.5 rounded-full bg-success"></span>
						Chrome 89+ (recommended)
					</li>
					<li class="flex items-center gap-2">
						<span class="w-1.5 h-1.5 rounded-full bg-success"></span>
						Edge 89+
					</li>
					<li class="flex items-center gap-2">
						<span class="w-1.5 h-1.5 rounded-full bg-success"></span>
						Opera 76+
					</li>
					<li class="flex items-center gap-2">
						<span class="w-1.5 h-1.5 rounded-full bg-error"></span>
						Firefox (not supported)
					</li>
					<li class="flex items-center gap-2">
						<span class="w-1.5 h-1.5 rounded-full bg-error"></span>
						Safari (not supported)
					</li>
				</ul>
			</div>
		{:else}
			<!-- WebUSB available -->
			<div class="space-y-4">
				<div class="p-4 rounded-xl bg-surface border border-border text-left space-y-3">
					<div class="text-xs font-bold text-foreground">Getting Started</div>
					<ol class="text-xs text-muted space-y-2 leading-relaxed">
						<li><span class="text-primary font-bold">1.</span> Connect Android phones via USB</li>
						<li><span class="text-primary font-bold">2.</span> Enable USB debugging on each phone</li>
						<li><span class="text-primary font-bold">3.</span> Click "Connect Phone" below</li>
						<li><span class="text-primary font-bold">4.</span> Accept the USB permission prompt</li>
						<li><span class="text-primary font-bold">5.</span> Accept "Allow USB debugging" on the phone</li>
					</ol>
				</div>

				<button
					class="w-full py-4 rounded-xl text-sm font-bold transition-colors min-h-[56px]
						{connecting ? 'bg-primary/50 text-foreground/50' : 'bg-primary text-background active:bg-primary-dim'}"
					onclick={handleConnect}
					disabled={connecting}
				>
					{#if connecting}
						<div class="flex items-center justify-center gap-2">
							<div class="w-4 h-4 border-2 border-current border-t-transparent rounded-full spinner"></div>
							Connecting...
						</div>
					{:else}
						Connect Phone
					{/if}
				</button>

				{#if hasDevices}
					<div class="text-xs text-success">
						{devices.length} device{devices.length !== 1 ? 's' : ''} connected
					</div>
				{/if}

				<div class="p-3 rounded-lg bg-surface/50 border border-border/50">
					<p class="text-[10px] text-muted leading-relaxed">
						<span class="font-bold">ADB Drivers:</span> Windows users may need
						<a href="https://developer.android.com/studio/run/win-usb" target="_blank" class="text-primary underline">Google USB drivers</a>
						or OEM drivers. macOS/Linux work out of the box.
					</p>
				</div>
			</div>
		{/if}
	</div>
</div>
