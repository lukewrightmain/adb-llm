<script lang="ts">
	import {
		getModelsData,
		getDownloadJobs,
		getDistributeJobs,
		getTargetDevicesString,
		fetchModels,
		downloadModel,
		distributeModel,
		pollJobProgress,
		startJobPolling,
		setGlobalError,
	} from '$lib/stores/app.svelte';
	import { onMount } from 'svelte';

	const models = $derived(getModelsData());
	const dlJobs = $derived(getDownloadJobs());
	const distJobs = $derived(getDistributeJobs());

	let repoId = $state('');
	let filename = $state('');
	let directUrl = $state('');
	let downloadMode = $state<'hf' | 'url'>('hf');
	let downloading = $state(false);

	onMount(() => {
		fetchModels();
		pollJobProgress();
	});

	async function handleDownload() {
		downloading = true;
		try {
			if (downloadMode === 'hf') {
				if (!repoId || !filename) { setGlobalError('Enter repo ID and filename'); return; }
				await downloadModel({ repo_id: repoId, filename });
			} else {
				if (!directUrl) { setGlobalError('Enter a URL'); return; }
				await downloadModel({ url: directUrl });
			}
			repoId = ''; filename = ''; directUrl = '';
		} catch (e) {
			setGlobalError(`Download failed: ${e}`);
		} finally {
			downloading = false;
		}
	}

	async function handleDistribute(name: string) {
		try {
			await distributeModel(name, getTargetDevicesString());
		} catch (e) {
			setGlobalError(`Distribute failed: ${e}`);
		}
	}

	function statusBadgeClass(status: string): string {
		switch (status) {
			case 'running': return 'bg-primary/15 text-primary';
			case 'completed': case 'done': return 'bg-success/15 text-success';
			case 'failed': case 'error': return 'bg-error/15 text-error';
			default: return 'bg-muted/15 text-muted';
		}
	}
</script>

<div class="h-full overflow-y-auto">
	<div class="p-4 space-y-5 max-w-lg mx-auto">

		<!-- Local Models -->
		<div>
			<h2 class="text-sm font-bold mb-2">Local Models</h2>
			{#if models.local_models.length === 0}
				<div class="text-xs text-muted py-4 text-center">No models downloaded yet</div>
			{:else}
				<div class="space-y-1.5">
					{#each models.local_models as m (m.name)}
						<div class="flex items-center gap-2 px-3 py-2 bg-surface border border-border rounded-lg min-h-[44px]">
							<div class="flex-1 min-w-0">
								<div class="text-xs truncate">{m.name}</div>
								<div class="text-[10px] text-muted">{m.size_mb}MB</div>
							</div>
							<button
								class="px-2.5 py-1.5 text-[10px] rounded-lg bg-primary/10 text-primary active:bg-primary/20 shrink-0 min-h-[32px]"
								onclick={() => handleDistribute(m.name)}
							>Distribute</button>
						</div>
					{/each}
				</div>
			{/if}

			<button
				class="mt-2 text-xs text-primary active:text-primary-dim"
				onclick={fetchModels}
			>Refresh models</button>
		</div>

		<!-- Download -->
		<div>
			<h2 class="text-sm font-bold mb-2">Download Model</h2>

			<!-- Mode toggle -->
			<div class="flex gap-1 mb-3">
				<button
					class="flex-1 py-2 text-xs rounded-lg min-h-[40px] transition-colors
						{downloadMode === 'hf' ? 'bg-primary/15 text-primary' : 'bg-surface text-muted'}"
					onclick={() => downloadMode = 'hf'}
				>HuggingFace</button>
				<button
					class="flex-1 py-2 text-xs rounded-lg min-h-[40px] transition-colors
						{downloadMode === 'url' ? 'bg-primary/15 text-primary' : 'bg-surface text-muted'}"
					onclick={() => downloadMode = 'url'}
				>Direct URL</button>
			</div>

			{#if downloadMode === 'hf'}
				<div class="space-y-2">
					<input
						bind:value={repoId}
						placeholder="Repo ID (e.g. TheBloke/...)"
						class="w-full bg-surface border border-border rounded-lg px-3 py-2.5 text-xs text-foreground min-h-[44px] outline-none focus:border-primary/50"
					/>
					<input
						bind:value={filename}
						placeholder="Filename (e.g. model.Q4_K_M.gguf)"
						class="w-full bg-surface border border-border rounded-lg px-3 py-2.5 text-xs text-foreground min-h-[44px] outline-none focus:border-primary/50"
					/>
				</div>
			{:else}
				<input
					bind:value={directUrl}
					placeholder="https://..."
					class="w-full bg-surface border border-border rounded-lg px-3 py-2.5 text-xs text-foreground min-h-[44px] outline-none focus:border-primary/50"
				/>
			{/if}

			<button
				class="w-full mt-3 py-2.5 rounded-xl text-xs font-bold min-h-[44px] transition-colors
					{downloading ? 'bg-primary/50 text-foreground/50' : 'bg-primary text-background active:bg-primary-dim'}"
				onclick={handleDownload}
				disabled={downloading}
			>
				{downloading ? 'Starting...' : 'Download'}
			</button>
		</div>

		<!-- Active Jobs -->
		{#if dlJobs.length > 0 || distJobs.length > 0}
			<div>
				<h2 class="text-sm font-bold mb-2">Jobs</h2>
				<div class="space-y-2">
					{#each [...dlJobs, ...distJobs] as job (job.job_id)}
						<div class="p-3 bg-surface border border-border rounded-lg">
							<div class="flex items-center gap-2 mb-1.5">
								<span class="text-xs truncate flex-1">{job.message || job.job_id}</span>
								<span class="px-1.5 py-0.5 rounded text-[9px] font-bold {statusBadgeClass(job.status)}">{job.status}</span>
							</div>
							{#if job.status === 'running'}
								<div class="w-full h-1.5 bg-border rounded-full overflow-hidden">
									<div
										class="h-full bg-primary rounded-full transition-all"
										style="width: {Math.round(job.progress * 100)}%"
									></div>
								</div>
								<div class="text-[9px] text-muted mt-1 text-right">{Math.round(job.progress * 100)}%</div>
							{/if}
						</div>
					{/each}
				</div>
			</div>
		{/if}

	</div>
</div>
