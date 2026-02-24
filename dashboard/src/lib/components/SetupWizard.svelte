<script lang="ts">
	import {
		getWebUsbSupported,
		getTcpProxyAvailable,
		addMultipleDevices,
		addTcpDevice,
		addMultipleTcpDevices,
		scanAndAddDevices,
		cancelScan,
		getScanProgress,
		isScanning,
		getManagedDevices,
	} from '$lib/stores/app.svelte';
	import { parseIpRange, describeIpInput } from '$lib/utils/ip-range';
	import { hasImportedKeys } from '$lib/services/adb-keys';
	import KeyManager from './KeyManager.svelte';

	const webUsbSupported = $derived(getWebUsbSupported());
	const tcpProxyAvailable = $derived(getTcpProxyAvailable());
	const devices = $derived(getManagedDevices());
	const hasDevices = $derived(devices.length > 0);
	const hasAnyConnection = $derived(webUsbSupported || tcpProxyAvailable);
	const scanning = $derived(isScanning());
	const progress = $derived(getScanProgress());

	let connectingUsb = $state(false);
	let connectingTcp = $state(false);
	let tcpInput = $state('');
	let tcpError = $state('');

	// Preview of what will be scanned/connected
	const inputPreview = $derived(tcpInput.trim() ? describeIpInput(tcpInput) : '');
	const parsedCount = $derived(tcpInput.trim() ? parseIpRange(tcpInput).length : 0);
	const isScannable = $derived(parsedCount > 10);

	async function handleConnectUsb() {
		connectingUsb = true;
		try {
			await addMultipleDevices();
		} catch (e) {
			console.error('Connect failed:', e);
		} finally {
			connectingUsb = false;
		}
	}

	async function handleConnectTcp() {
		tcpError = '';
		const entries = parseIpRange(tcpInput.trim());
		if (entries.length === 0) {
			tcpError = 'No valid IPs found';
			return;
		}

		connectingTcp = true;
		try {
			if (entries.length === 1) {
				await addTcpDevice(entries[0].host, entries[0].port);
			} else {
				await addMultipleTcpDevices(entries.map(e => ({ host: e.host, port: e.port })));
			}
			tcpInput = '';
		} catch (e) {
			tcpError = e instanceof Error ? e.message : String(e);
		} finally {
			connectingTcp = false;
		}
	}

	async function handleScan() {
		tcpError = '';
		const entries = parseIpRange(tcpInput.trim());
		if (entries.length === 0) {
			tcpError = 'No valid IPs to scan';
			return;
		}
		await scanAndAddDevices(entries.map(e => e.host), entries[0]?.port ?? 5555);
	}

	function handleTcpKeydown(e: KeyboardEvent) {
		if (e.key === 'Enter' && !e.shiftKey) {
			e.preventDefault();
			if (isScannable) {
				handleScan();
			} else {
				handleConnectTcp();
			}
		}
	}
</script>

<div class="h-full flex items-center justify-center p-6">
	<div class="max-w-md w-full space-y-6 text-center">
		<!-- Logo -->
		<div class="space-y-2">
			<div class="text-4xl font-bold text-primary">CellSwarm</div>
			<div class="text-xs text-muted">Phone-Cluster LLM Inference</div>
		</div>

		{#if !hasAnyConnection}
			<div class="p-4 rounded-xl bg-error/10 border border-error/20 text-left space-y-3">
				<div class="text-sm font-bold text-error">No Connection Available</div>
				<p class="text-xs text-muted leading-relaxed">
					WebUSB requires HTTPS or localhost. The TCP proxy is not running.
				</p>
				<div class="text-xs text-muted space-y-1">
					<p class="font-bold">To connect TCP/IP devices:</p>
					<p>Start the proxy: <code class="px-1.5 py-0.5 rounded bg-surface text-foreground">node ws-proxy.mjs</code></p>
					<p class="font-bold mt-2">To connect USB devices:</p>
					<p>Use Chrome/Edge with HTTPS, or enable the Chrome flag for insecure origins.</p>
				</div>
			</div>
		{:else}
			<div class="space-y-4">
				<!-- ADB Key Import -->
				<div class="p-4 rounded-xl bg-surface border border-border text-left">
					<KeyManager />
				</div>

				<!-- TCP/IP connection -->
				{#if tcpProxyAvailable}
					<div class="p-4 rounded-xl bg-surface border border-border text-left space-y-3">
						<div class="flex items-center gap-2">
							<div class="w-2 h-2 rounded-full bg-success"></div>
							<span class="text-xs font-bold text-foreground">TCP/IP Devices (Ethernet)</span>
						</div>
						<p class="text-xs text-muted leading-relaxed">
							Enter IPs, ranges, or CIDR to connect or scan for ADB devices.
						</p>
						<div class="space-y-2">
							<input
								type="text"
								bind:value={tcpInput}
								onkeydown={handleTcpKeydown}
								placeholder="10.105.0.0/24 or 10.105.0.12-48"
								disabled={scanning}
								class="w-full px-3 py-2.5 text-xs rounded-lg bg-background border border-border text-foreground placeholder:text-muted/50 focus:outline-none focus:border-primary disabled:opacity-50"
							/>
							{#if tcpError}
								<div class="text-xs text-error">{tcpError}</div>
							{/if}

							<!-- Preview -->
							{#if inputPreview && !scanning}
								<div class="text-[10px] text-muted">
									{inputPreview}
									{#if isScannable}
										— will scan for active ADB daemons
									{/if}
								</div>
							{/if}

							<!-- Scan progress -->
							{#if progress && (progress.type === 'start' || progress.type === 'progress' || progress.type === 'done')}
								<div class="space-y-1.5">
									<div class="flex items-center justify-between text-[10px]">
										<span class="text-muted">
											{#if progress.type === 'done'}
												Scan complete
											{:else}
												Scanning {progress.scanned ?? 0}/{progress.total ?? 0}
											{/if}
										</span>
										<span class="text-success font-bold">{progress.found ?? 0} found</span>
									</div>
									<div class="h-1.5 bg-border rounded-full overflow-hidden">
										<div
											class="h-full rounded-full transition-all bg-primary"
											style="width: {progress.total ? ((progress.scanned ?? 0) / progress.total * 100) : 0}%"
										></div>
									</div>
									{#if progress.type === 'done' && progress.elapsedMs}
										<div class="text-[10px] text-muted">{(progress.elapsedMs / 1000).toFixed(1)}s elapsed</div>
									{/if}
								</div>
							{/if}

							<!-- Action buttons -->
							{#if scanning}
								<button
									class="w-full py-3 rounded-xl text-sm font-bold bg-error/10 text-error active:bg-error/20 min-h-[48px]"
									onclick={cancelScan}
								>
									Cancel Scan
								</button>
							{:else if isScannable}
								<div class="flex gap-2">
									<button
										class="flex-1 py-3 rounded-xl text-sm font-bold bg-primary text-background active:bg-primary-dim min-h-[48px]"
										onclick={handleScan}
									>
										Scan {parsedCount} IPs
									</button>
									<button
										class="py-3 px-4 rounded-xl text-sm font-bold bg-primary/10 text-primary active:bg-primary/20 min-h-[48px]"
										onclick={handleConnectTcp}
										disabled={connectingTcp}
										title="Connect to all IPs without scanning"
									>
										{#if connectingTcp}
											<div class="w-4 h-4 border-2 border-current border-t-transparent rounded-full spinner"></div>
										{:else}
											Force All
										{/if}
									</button>
								</div>
							{:else}
								<button
									class="w-full py-3 rounded-xl text-sm font-bold min-h-[48px]
										{connectingTcp ? 'bg-primary/50 text-foreground/50' : 'bg-primary text-background active:bg-primary-dim'}"
									onclick={handleConnectTcp}
									disabled={connectingTcp || !tcpInput.trim()}
								>
									{#if connectingTcp}
										<div class="flex items-center justify-center gap-2">
											<div class="w-4 h-4 border-2 border-current border-t-transparent rounded-full spinner"></div>
											Connecting...
										</div>
									{:else}
										Connect
									{/if}
								</button>
							{/if}
						</div>
						<div class="text-[10px] text-muted leading-relaxed space-y-1">
							<p><span class="font-bold">Formats:</span></p>
							<p><code class="px-1 rounded bg-background">10.105.0.12</code> — single device</p>
							<p><code class="px-1 rounded bg-background">10.105.0.12-48</code> — last octet range</p>
							<p><code class="px-1 rounded bg-background">10.105.0.0/24</code> — CIDR subnet (scans 254 IPs)</p>
							<p><code class="px-1 rounded bg-background">10.105.0.12-10.105.0.48</code> — full range</p>
							<p><code class="px-1 rounded bg-background">10.0-1.0-255.1</code> — multi-octet range</p>
						</div>
					</div>
				{/if}

				<!-- USB connection -->
				{#if webUsbSupported}
					<div class="p-4 rounded-xl bg-surface border border-border text-left space-y-3">
						<div class="flex items-center gap-2">
							<div class="w-2 h-2 rounded-full bg-primary"></div>
							<span class="text-xs font-bold text-foreground">USB Devices</span>
						</div>
						<ol class="text-xs text-muted space-y-1.5 leading-relaxed">
							<li><span class="text-primary font-bold">1.</span> Connect phones via USB</li>
							<li><span class="text-primary font-bold">2.</span> Enable USB debugging</li>
							<li><span class="text-primary font-bold">3.</span> Click below — select devices one by one until done</li>
						</ol>
						<button
							class="w-full py-3 rounded-xl text-sm font-bold transition-colors min-h-[48px]
								{connectingUsb ? 'bg-primary/50 text-foreground/50' : 'bg-primary/10 text-primary border border-primary/30 active:bg-primary/20'}"
							onclick={handleConnectUsb}
							disabled={connectingUsb}
						>
							{#if connectingUsb}
								<div class="flex items-center justify-center gap-2">
									<div class="w-4 h-4 border-2 border-current border-t-transparent rounded-full spinner"></div>
									Select devices (cancel dialog when done)
								</div>
							{:else}
								Connect USB Devices
							{/if}
						</button>
					</div>
				{/if}

				{#if !webUsbSupported && tcpProxyAvailable}
					<div class="p-3 rounded-lg bg-surface/50 border border-border/50">
						<p class="text-[10px] text-muted leading-relaxed">
							<span class="font-bold">Note:</span> WebUSB not available (requires HTTPS).
							USB devices can't be connected, but TCP/IP works fine.
						</p>
					</div>
				{/if}

				{#if hasDevices}
					<div class="text-xs text-success">
						{devices.length} device{devices.length !== 1 ? 's' : ''} connected
					</div>
				{/if}
			</div>
		{/if}
	</div>
</div>
