<script lang="ts">
	import {
		getManagedDevices,
		getDeviceModels,
		refreshModels,
		isRingForming,
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
	let refreshing = $state(false);

	onMount(() => {
		if (!isRingForming()) handleRefresh();
	});

	async function handleRefresh() {
		refreshing = true;
		try {
			await refreshModels();
		} finally {
			refreshing = false;
		}
	}

	async function handleDownload() {
		if (!directUrl || !filename) {
			setGlobalError('Enter URL and filename');
			return;
		}
		downloading = true;
		try {
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

	async function handleDelete(serial: string, modelPath: string) {
		const device = devices.find(d => d.serial === serial);
		if (!device?.adb) return;

		try {
			await deleteModel(device.adb, modelPath);
			await refreshModels();
		} catch (e) {
			setGlobalError(`Delete failed: ${e}`);
		}
	}

	function formatSize(sizeMb: number): string {
		if (sizeMb >= 1024) return `${(sizeMb / 1024).toFixed(1)} GB`;
		return `${sizeMb} MB`;
	}

	function shortDir(dir: string): string {
		if (dir.includes('cellswarm/models')) return 'cellswarm';
		if (dir.includes('adb-llm/models')) return 'adb-llm';
		if (dir.includes('/sdcard/Download')) return 'sdcard';
		if (dir.includes('/sdcard/Models')) return 'sdcard';
		return dir.split('/').pop() ?? dir;
	}

	function dirColor(dir: string): string {
		if (dir.includes('cellswarm')) return 'bg-primary/15 text-primary';
		if (dir.includes('adb-llm')) return 'bg-success/15 text-success';
		if (dir.includes('sdcard')) return 'bg-warning/15 text-warning';
		return 'bg-muted/15 text-muted';
	}

	function extBadge(ext: string): string {
		if (ext === 'gguf') return 'bg-primary/15 text-primary';
		if (ext === 'safetensors') return 'bg-success/15 text-success';
		if (ext === 'bin') return 'bg-warning/15 text-warning';
		return 'bg-muted/15 text-muted';
	}

	// Summary: total models across all devices
	const totalModels = $derived(() => {
		let total = 0;
		for (const models of deviceModels.values()) total += models.length;
		return total;
	});

	// Unique models across all devices
	const uniqueModelNames = $derived(() => {
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
				<div>
					<h2 class="text-sm font-bold">Device Models</h2>
					{#if totalModels() > 0}
						<p class="text-[10px] text-muted">{totalModels()} files across {connectedDevices.length} device{connectedDevices.length !== 1 ? 's' : ''} ({uniqueModelNames().length} unique)</p>
					{/if}
				</div>
				<button
					class="text-xs text-primary active:text-primary-dim flex items-center gap-1.5"
					onclick={handleRefresh}
					disabled={refreshing}
				>
					{#if refreshing}
						<div class="w-3 h-3 border border-current border-t-transparent rounded-full spinner"></div>
						Scanning...
					{:else}
						<svg class="w-3.5 h-3.5" viewBox="0 0 20 20" fill="currentColor">
							<path fill-rule="evenodd" d="M4 2a1 1 0 011 1v2.101a7.002 7.002 0 0111.601 2.566 1 1 0 11-1.885.666A5.002 5.002 0 005.999 7H9a1 1 0 010 2H4a1 1 0 01-1-1V3a1 1 0 011-1zm.008 9.057a1 1 0 011.276.61A5.002 5.002 0 0014.001 13H11a1 1 0 110-2h5a1 1 0 011 1v5a1 1 0 11-2 0v-2.101a7.002 7.002 0 01-11.601-2.566 1 1 0 01.61-1.276z" clip-rule="evenodd"/>
						</svg>
						Scan All
					{/if}
				</button>
			</div>

			{#if connectedDevices.length === 0}
				<div class="text-xs text-muted py-8 text-center">
					<div class="text-2xl mb-2">No devices connected</div>
					<p>Connect devices in the Devices tab first.</p>
				</div>
			{:else}
				<div class="space-y-3">
					{#each connectedDevices as dev (dev.serial)}
						{@const models = deviceModels.get(dev.serial) ?? []}
						<div class="p-3 bg-surface border border-border rounded-lg">
							<div class="flex items-center justify-between mb-2">
								<div class="text-xs font-bold">{dev.shortSerial}
									<span class="font-normal text-muted ml-1">{dev.info?.model ?? ''}</span>
								</div>
								{#if models.length > 0}
									<span class="text-[10px] text-muted">{models.length} file{models.length !== 1 ? 's' : ''}</span>
								{/if}
							</div>
							{#if models.length === 0}
								<div class="text-[10px] text-muted py-2 text-center">No models found</div>
							{:else}
								<div class="space-y-1.5">
									{#each models as m (m.path)}
										<div class="flex items-center gap-2 text-[10px] group">
											<!-- Extension badge -->
											<span class="px-1 py-0.5 rounded text-[8px] uppercase font-bold shrink-0 {extBadge(m.ext)}">{m.ext}</span>
											<!-- Model name -->
											<span class="flex-1 truncate text-foreground" title={m.path}>{m.name}</span>
											<!-- Directory badge -->
											<span class="px-1 py-0.5 rounded text-[8px] shrink-0 {dirColor(m.dir)}">{shortDir(m.dir)}</span>
											<!-- Size -->
											<span class="text-muted shrink-0 w-14 text-right">{formatSize(m.sizeMb)}</span>
											<!-- Delete button -->
											<button
												class="text-error/40 active:text-error shrink-0 opacity-0 group-hover:opacity-100 transition-opacity"
												onclick={() => handleDelete(dev.serial, m.path)}
												title="Delete {m.name}"
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

		<!-- Search paths info -->
		<div class="p-3 bg-surface/50 rounded-lg border border-border/50">
			<h3 class="text-[10px] font-bold text-muted mb-1">Search Locations</h3>
			<div class="text-[10px] text-muted leading-relaxed space-y-0.5">
				<div><span class="px-1 py-0.5 rounded bg-primary/15 text-primary text-[8px]">cellswarm</span> /data/local/tmp/cellswarm/models/</div>
				<div><span class="px-1 py-0.5 rounded bg-success/15 text-success text-[8px]">adb-llm</span> /data/local/tmp/adb-llm/models/</div>
				<div class="text-muted/60">Also: /data/local/tmp/models, /sdcard/Download, /sdcard/Models</div>
			</div>
			<p class="text-[10px] text-muted mt-1.5">File types: .gguf, .bin, .safetensors, .onnx, .pt, .pth, .ggml</p>
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
