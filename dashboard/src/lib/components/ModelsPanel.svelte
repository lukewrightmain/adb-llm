<script lang="ts">
	import {
		getManagedDevices,
		getDeviceModels,
		refreshModels,
		setGlobalError,
	} from '$lib/stores/app.svelte';
	import { downloadModelOnDevice, deleteModel } from '$lib/services/model-manager';
	import { onMount } from 'svelte';

	const devices = $derived(getManagedDevices());
	const deviceModels = $derived(getDeviceModels());
	const connectedDevices = $derived(devices.filter(d => d.adb));

	let directUrl = $state('');
	let filename = $state('');
	let downloading = $state(false);

	onMount(() => {
		refreshModels();
	});

	async function handleDownload() {
		if (!directUrl || !filename) {
			setGlobalError('Enter URL and filename');
			return;
		}
		downloading = true;
		try {
			// Download to all connected devices in parallel
			await Promise.all(
				connectedDevices.map(async (d) => {
					try {
						await downloadModelOnDevice(d.adb!, directUrl, filename);
					} catch (e) {
						console.error(`Download failed on ${d.serial}:`, e);
					}
				})
			);
			directUrl = '';
			filename = '';
			setGlobalError(null);
		} catch (e) {
			setGlobalError(`Download failed: ${e}`);
		} finally {
			downloading = false;
		}
	}

	async function handleDelete(serial: string, modelName: string) {
		const device = devices.find(d => d.serial === serial);
		if (!device?.adb) return;

		try {
			await deleteModel(device.adb, modelName);
			await refreshModels();
		} catch (e) {
			setGlobalError(`Delete failed: ${e}`);
		}
	}

	// Get unique model names across all devices
	const allModelNames = $derived(() => {
		const names = new Set<string>();
		for (const models of deviceModels.values()) {
			for (const m of models) names.add(m.name);
		}
		return Array.from(names).sort();
	});
</script>

<div class="h-full overflow-y-auto">
	<div class="p-4 space-y-5 max-w-lg mx-auto">

		<!-- Models per device -->
		<div>
			<div class="flex items-center justify-between mb-2">
				<h2 class="text-sm font-bold">Device Models</h2>
				<button
					class="text-xs text-primary active:text-primary-dim"
					onclick={refreshModels}
				>Refresh</button>
			</div>

			{#if connectedDevices.length === 0}
				<div class="text-xs text-muted py-4 text-center">No devices connected</div>
			{:else}
				<div class="space-y-3">
					{#each connectedDevices as dev (dev.serial)}
						{@const models = deviceModels.get(dev.serial) ?? []}
						<div class="p-3 bg-surface border border-border rounded-lg">
							<div class="text-xs font-bold mb-2">{dev.shortSerial}
								<span class="font-normal text-muted ml-1">{dev.info?.model ?? ''}</span>
							</div>
							{#if models.length === 0}
								<div class="text-[10px] text-muted">No models</div>
							{:else}
								<div class="space-y-1">
									{#each models as m (m.name)}
										<div class="flex items-center gap-2 text-[10px]">
											<span class="flex-1 truncate text-foreground">{m.name}</span>
											<span class="text-muted shrink-0">{m.sizeMb > 1024 ? `${(m.sizeMb / 1024).toFixed(1)}GB` : `${m.sizeMb}MB`}</span>
											<button
												class="text-error/60 active:text-error shrink-0"
												onclick={() => handleDelete(dev.serial, m.name)}
											>
												<svg class="w-3.5 h-3.5" viewBox="0 0 20 20" fill="currentColor">
													<path fill-rule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clip-rule="evenodd"/>
												</svg>
											</button>
										</div>
									{/each}
								</div>
							{/if}
						</div>
					{/each}
				</div>
			{/if}
		</div>

		<!-- Download Model -->
		<div>
			<h2 class="text-sm font-bold mb-2">Download Model to Devices</h2>
			<p class="text-[10px] text-muted mb-3">Phones download directly via wget. Enter the GGUF URL.</p>

			<div class="space-y-2">
				<input
					bind:value={directUrl}
					placeholder="https://huggingface.co/.../model.gguf"
					class="w-full bg-surface border border-border rounded-lg px-3 py-2.5 text-xs text-foreground min-h-[44px] outline-none focus:border-primary/50"
				/>
				<input
					bind:value={filename}
					placeholder="Filename (e.g. deepseek-coder-33b.Q4_K_M.gguf)"
					class="w-full bg-surface border border-border rounded-lg px-3 py-2.5 text-xs text-foreground min-h-[44px] outline-none focus:border-primary/50"
				/>
			</div>

			<button
				class="w-full mt-3 py-2.5 rounded-xl text-xs font-bold min-h-[44px] transition-colors
					{downloading ? 'bg-primary/50 text-foreground/50' : 'bg-primary text-background active:bg-primary-dim'}"
				onclick={handleDownload}
				disabled={downloading}
			>
				{downloading ? 'Starting downloads...' : `Download to ${connectedDevices.length} device${connectedDevices.length !== 1 ? 's' : ''}`}
			</button>
		</div>
	</div>
</div>
