<script lang="ts">
	import {
		getModelsData,
		getDownloadJobs,
		getDistributeJobs,
		fetchModels,
		downloadModel,
		distributeModel,
		pollJobProgress,
	} from '$lib/stores/app.svelte';

	const models = $derived(getModelsData());
	const dlJobs = $derived(getDownloadJobs());
	const distJobs = $derived(getDistributeJobs());

	let repoId = $state('');
	let filename = $state('');
	let downloadUrl = $state('');
	let downloadLoading = $state(false);
	let distributeLoading = $state('');
	let error = $state('');

	// Poll job progress when there are active jobs
	let jobPollInterval: ReturnType<typeof setInterval> | null = null;

	$effect(() => {
		fetchModels();
		return () => {
			if (jobPollInterval) clearInterval(jobPollInterval);
		};
	});

	async function handleHfDownload() {
		if (!repoId) return;
		downloadLoading = true;
		error = '';
		try {
			await downloadModel({ repo_id: repoId, filename: filename || undefined });
			repoId = '';
			filename = '';
			startJobPolling();
		} catch (e) {
			error = String(e);
		} finally {
			downloadLoading = false;
		}
	}

	async function handleUrlDownload() {
		if (!downloadUrl) return;
		downloadLoading = true;
		error = '';
		try {
			await downloadModel({ url: downloadUrl });
			downloadUrl = '';
			startJobPolling();
		} catch (e) {
			error = String(e);
		} finally {
			downloadLoading = false;
		}
	}

	async function handleDistribute(modelName: string) {
		distributeLoading = modelName;
		error = '';
		try {
			await distributeModel(modelName);
			startJobPolling();
		} catch (e) {
			error = String(e);
		} finally {
			distributeLoading = '';
		}
	}

	function startJobPolling() {
		if (jobPollInterval) return;
		jobPollInterval = setInterval(async () => {
			await pollJobProgress();
			// Stop polling when all jobs are done
			const allDone = [...dlJobs, ...distJobs].every(
				(j) => j.status === 'completed' || j.status === 'failed'
			);
			if (allDone && jobPollInterval) {
				clearInterval(jobPollInterval);
				jobPollInterval = null;
				fetchModels();
			}
		}, 2000);
	}
</script>

<div class="space-y-4">
	<!-- Local models -->
	<div class="border border-border rounded-lg bg-surface p-4">
		<h3 class="text-sm font-bold text-foreground mb-3">Local Models</h3>
		{#if models.local_models.length === 0}
			<p class="text-xs text-muted">No GGUF models found in ~/.cellswarm/models/</p>
		{:else}
			<div class="space-y-2">
				{#each models.local_models as m}
					<div class="flex items-center justify-between text-xs bg-background rounded px-3 py-2">
						<div>
							<span class="text-foreground font-bold">{m.name}</span>
							<span class="text-muted ml-2">{m.size_mb}MB</span>
						</div>
						<button
							class="px-2 py-1 rounded text-[10px] bg-primary/20 text-primary hover:bg-primary/30 transition-colors disabled:opacity-50"
							onclick={() => handleDistribute(m.name)}
							disabled={distributeLoading === m.name}
						>
							{distributeLoading === m.name ? 'Pushing...' : 'Distribute'}
						</button>
					</div>
				{/each}
			</div>
		{/if}
	</div>

	<!-- Device models -->
	<div class="border border-border rounded-lg bg-surface p-4">
		<h3 class="text-sm font-bold text-foreground mb-3">Device Models</h3>
		{#if Object.keys(models.device_models).length === 0}
			<p class="text-xs text-muted">No device data</p>
		{:else}
			<div class="space-y-1 text-xs">
				{#each Object.entries(models.device_models) as [serial, mods]}
					<div class="flex items-start gap-2">
						<span class="text-muted w-16 shrink-0">{serial}</span>
						<span class="text-foreground">{mods.length > 0 ? mods.join(', ') : 'none'}</span>
					</div>
				{/each}
			</div>
		{/if}
	</div>

	<!-- Download from HuggingFace -->
	<div class="border border-border rounded-lg bg-surface p-4">
		<h3 class="text-sm font-bold text-foreground mb-3">Download Model</h3>
		<div class="space-y-2">
			<div>
				<label class="block text-[10px] text-muted mb-1 uppercase tracking-wider">HuggingFace Repo</label>
				<input
					type="text"
					class="w-full bg-background border border-border rounded px-2 py-1.5 text-xs text-foreground"
					bind:value={repoId}
					placeholder="TheBloke/deepseek-coder-33B-instruct-GGUF"
				/>
			</div>
			<div>
				<label class="block text-[10px] text-muted mb-1 uppercase tracking-wider">Filename (optional)</label>
				<input
					type="text"
					class="w-full bg-background border border-border rounded px-2 py-1.5 text-xs text-foreground"
					bind:value={filename}
					placeholder="*.Q4_K_M.gguf"
				/>
			</div>
			<button
				class="w-full px-3 py-1.5 rounded bg-primary/20 text-primary border border-primary/30 text-xs hover:bg-primary/30 transition-colors disabled:opacity-50"
				onclick={handleHfDownload}
				disabled={downloadLoading || !repoId}
			>
				{downloadLoading ? 'Starting...' : 'Download from HF'}
			</button>

			<div class="text-[10px] text-muted text-center">or</div>

			<div>
				<label class="block text-[10px] text-muted mb-1 uppercase tracking-wider">Direct URL</label>
				<input
					type="text"
					class="w-full bg-background border border-border rounded px-2 py-1.5 text-xs text-foreground"
					bind:value={downloadUrl}
					placeholder="https://..."
				/>
			</div>
			<button
				class="w-full px-3 py-1.5 rounded bg-primary/20 text-primary border border-primary/30 text-xs hover:bg-primary/30 transition-colors disabled:opacity-50"
				onclick={handleUrlDownload}
				disabled={downloadLoading || !downloadUrl}
			>
				Download from URL
			</button>
		</div>
	</div>

	<!-- Active jobs -->
	{#if dlJobs.length > 0 || distJobs.length > 0}
		<div class="border border-border rounded-lg bg-surface p-4">
			<h3 class="text-sm font-bold text-foreground mb-3">Active Jobs</h3>
			<div class="space-y-2">
				{#each [...dlJobs, ...distJobs] as job}
					<div class="text-xs bg-background rounded px-3 py-2">
						<div class="flex justify-between mb-1">
							<span class="text-foreground">{job.job_id}</span>
							<span class="{job.status === 'completed' ? 'text-success' : job.status === 'failed' ? 'text-error' : 'text-primary'}">{job.status}</span>
						</div>
						<div class="text-muted">{job.message}</div>
						{#if job.status === 'running'}
							<div class="w-full h-1 bg-border rounded-full mt-1 overflow-hidden">
								<div class="h-full bg-primary rounded-full transition-all" style="width: {job.progress * 100}%"></div>
							</div>
						{/if}
					</div>
				{/each}
			</div>
		</div>
	{/if}

	{#if error}
		<div class="text-xs text-error bg-error/10 rounded px-3 py-2">{error}</div>
	{/if}
</div>
