<script lang="ts">
	import {
		getSettings,
		updateSettings,
		getModelsData,
		fetchModels,
	} from '$lib/stores/app.svelte';
	import { onMount } from 'svelte';

	const settings = $derived(getSettings());
	const models = $derived(getModelsData());

	onMount(() => { fetchModels(); });

	function toggle(key: 'speculative' | 'prefetch') {
		updateSettings({ [key]: !settings[key] });
	}

	function setNum(key: 'draftMax' | 'totalLayers' | 'contextSize', e: Event) {
		const val = parseInt((e.target as HTMLInputElement).value);
		if (!isNaN(val)) updateSettings({ [key]: val });
	}

	function setModel(key: 'defaultModel' | 'defaultDraftModel', e: Event) {
		updateSettings({ [key]: (e.target as HTMLSelectElement).value });
	}
</script>

<div class="h-full overflow-y-auto">
	<div class="p-4 space-y-5 max-w-lg mx-auto">
		<h2 class="text-sm font-bold">Ring Settings</h2>
		<p class="text-[10px] text-muted -mt-3">These are used as defaults when starting a ring. Saved automatically.</p>

		<!-- Default Model -->
		<div>
			<label class="block text-xs text-muted mb-1" for="set-model">Default Target Model</label>
			<select
				id="set-model"
				value={settings.defaultModel}
				onchange={(e) => setModel('defaultModel', e)}
				class="w-full bg-surface border border-border rounded-lg px-3 py-2.5 text-xs text-foreground min-h-[44px]"
			>
				<option value="">None (choose each time)</option>
				{#each models.local_models as m}
					<option value={m.name}>{m.name} ({m.size_mb}MB)</option>
				{/each}
			</select>
		</div>

		<!-- Speculative Decoding -->
		<div class="p-3 bg-surface rounded-lg border border-border space-y-3">
			<label class="flex items-center gap-3 min-h-[44px]">
				<input
					type="checkbox"
					checked={settings.speculative}
					onchange={() => toggle('speculative')}
					class="w-5 h-5 rounded accent-primary"
				/>
				<div>
					<span class="text-xs font-bold">Speculative Decoding</span>
					<p class="text-[10px] text-muted">Use a small draft model to predict tokens, verify in batch. ~2-6x faster.</p>
				</div>
			</label>

			{#if settings.speculative}
				<div>
					<label class="block text-xs text-muted mb-1" for="set-draft">Draft Model</label>
					<select
						id="set-draft"
						value={settings.defaultDraftModel}
						onchange={(e) => setModel('defaultDraftModel', e)}
						class="w-full bg-surface border border-border rounded-lg px-3 py-2.5 text-xs text-foreground min-h-[44px]"
					>
						<option value="">None</option>
						{#each models.local_models as m}
							<option value={m.name}>{m.name} ({m.size_mb}MB)</option>
						{/each}
					</select>
				</div>

				<div>
					<label class="block text-xs text-muted mb-1" for="set-dmax">Draft Max Tokens</label>
					<div class="flex items-center gap-3">
						<input
							id="set-dmax"
							type="range" min={4} max={48} step={4}
							value={settings.draftMax}
							oninput={(e) => setNum('draftMax', e)}
							class="flex-1 accent-primary"
						/>
						<span class="text-xs w-8 text-center">{settings.draftMax}</span>
					</div>
					<p class="text-[10px] text-muted mt-1">Higher = more speculative tokens per cycle. 24 is optimal for 12 phones.</p>
				</div>
			{/if}
		</div>

		<!-- Layers -->
		<div>
			<label class="block text-xs text-muted mb-1" for="set-layers">Total Layers</label>
			<input
				id="set-layers"
				type="number"
				value={settings.totalLayers}
				onchange={(e) => setNum('totalLayers', e)}
				min={1} max={200}
				class="w-full bg-surface border border-border rounded-lg px-3 py-2.5 text-xs text-foreground min-h-[44px]"
			/>
			<p class="text-[10px] text-muted mt-1">DeepSeek 33B = 62, 7B = 32, 1.3B = 24</p>
		</div>

		<!-- Context Size -->
		<div>
			<label class="block text-xs text-muted mb-1" for="set-ctx">Context Size</label>
			<div class="flex gap-2">
				{#each [512, 1024, 2048, 4096] as val}
					<button
						class="flex-1 py-2 text-xs rounded-lg min-h-[40px] transition-colors
							{settings.contextSize === val ? 'bg-primary/15 text-primary border border-primary/30' : 'bg-surface border border-border text-muted'}"
						onclick={() => updateSettings({ contextSize: val })}
					>{val}</button>
				{/each}
			</div>
			<p class="text-[10px] text-muted mt-1">Lower = less RAM per phone. 2048 for chat, 512 for benchmarks.</p>
		</div>

		<!-- Prefetch -->
		<label class="flex items-center gap-3 min-h-[44px]">
			<input
				type="checkbox"
				checked={settings.prefetch}
				onchange={() => toggle('prefetch')}
				class="w-5 h-5 rounded accent-primary"
			/>
			<div>
				<span class="text-xs">Prefetch layers</span>
				<p class="text-[10px] text-muted">Overlap layer loading with computation. Usually faster.</p>
			</div>
		</label>

		<!-- Info -->
		<div class="p-3 bg-surface/50 rounded-lg border border-border/50">
			<h3 class="text-[10px] font-bold text-muted mb-1">Production Config</h3>
			<p class="text-[10px] text-muted leading-relaxed">
				Best results: 12 phones, speculative with d24, Q4_K_M target + 1.3B Q4_K_M draft, context 2048, --no-mmap, prefetch, -t 4, taskset f0.
				Peak: 6.1 tok/s.
			</p>
		</div>
	</div>
</div>
