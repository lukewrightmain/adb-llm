<script lang="ts">
	import {
		importKeyFromFile,
		importKey,
		listKeys,
		removeKey,
		hasImportedKeys,
		type StoredKeyMeta,
	} from '$lib/services/adb-keys';

	let keys = $state<StoredKeyMeta[]>(listKeys());
	let importing = $state(false);
	let error = $state('');
	let success = $state('');
	let showPaste = $state(false);
	let pasteText = $state('');
	let showQuickImport = $state(false);
	let quickImporting = $state<string | null>(null);

	let fileInput: HTMLInputElement;

	// Known keysets available at /keys/
	const KNOWN_KEYSETS = [
		{ file: 'F721.pem', name: 'F721', desc: 'Box1 (original)' },
		{ file: 'Giga_S20.pem', name: 'Giga + S20', desc: 'Giga + S20 phones' },
		{ file: 'Maybe-F926-Syj.pem', name: 'Maybe-F926-Syj', desc: 'F926 Z Fold3' },
		{ file: 'test.pem', name: 'test', desc: 'Test keyset' },
	];

	function refresh() {
		keys = listKeys();
	}

	async function handleFiles(e: Event) {
		const input = e.target as HTMLInputElement;
		if (!input.files?.length) return;

		importing = true;
		error = '';
		success = '';

		let imported = 0;
		let errors: string[] = [];

		for (const file of Array.from(input.files)) {
			// Skip public key files and zip files
			if (file.name.endsWith('.pub')) {
				errors.push(`${file.name}: Skipped — public key not needed, only the private key (adbkey).`);
				continue;
			}
			if (file.name.endsWith('.zip')) {
				errors.push(`${file.name}: Skipped — upload the adbkey file directly, not the zip.`);
				continue;
			}
			try {
				await importKeyFromFile(file);
				imported++;
			} catch (err) {
				errors.push(`${file.name}: ${err instanceof Error ? err.message : String(err)}`);
			}
		}

		if (imported > 0) {
			success = `Imported ${imported} key${imported > 1 ? 's' : ''}`;
		}
		if (errors.length > 0) {
			error = errors.join('\n');
		}

		input.value = '';
		importing = false;
		refresh();
	}

	async function handlePaste() {
		if (!pasteText.trim()) return;

		importing = true;
		error = '';
		success = '';

		try {
			const meta = await importKey(pasteText.trim());
			success = `Imported key: ${meta.name} (${meta.fingerprint})`;
			pasteText = '';
			showPaste = false;
		} catch (err) {
			error = err instanceof Error ? err.message : String(err);
		}

		importing = false;
		refresh();
	}

	async function handleQuickImport(keyset: typeof KNOWN_KEYSETS[0]) {
		quickImporting = keyset.file;
		error = '';
		success = '';

		try {
			const resp = await fetch(`/keys/${keyset.file}`);
			if (!resp.ok) throw new Error(`Failed to fetch key: ${resp.status}`);
			const pemText = await resp.text();
			await importKey(pemText, keyset.name);
			success = `Imported "${keyset.name}" keyset`;
		} catch (err) {
			error = err instanceof Error ? err.message : String(err);
		}

		quickImporting = null;
		refresh();
	}

	function handleRemove(fingerprint: string) {
		removeKey(fingerprint);
		refresh();
		success = '';
		error = '';
	}

	function formatDate(ts: number): string {
		return new Date(ts).toLocaleDateString(undefined, {
			month: 'short',
			day: 'numeric',
			hour: '2-digit',
			minute: '2-digit',
		});
	}
</script>

<div class="space-y-3">
	<div class="flex items-center justify-between">
		<div>
			<span class="text-xs font-bold text-foreground">ADB Keys</span>
			<p class="text-[10px] text-muted">Import multiple keysets — each device tries all keys until one works.</p>
		</div>
		<span class="text-[10px] text-muted">{keys.length} key{keys.length !== 1 ? 's' : ''}</span>
	</div>

	<!-- Imported keys list -->
	{#if keys.length > 0}
		<div class="space-y-1.5">
			{#each keys as key}
				<div class="flex items-center justify-between p-2.5 rounded-lg bg-background border border-border">
					<div class="min-w-0 flex-1">
						<div class="flex items-center gap-2">
							<span class="text-xs font-medium text-foreground truncate">{key.name}</span>
							{#if key.format}
								<span class="text-[9px] px-1.5 py-0.5 rounded bg-primary/10 text-primary">{key.format}</span>
							{/if}
						</div>
						<div class="text-[10px] text-muted">
							<span class="font-mono">{key.fingerprint}</span>
							<span class="mx-1">&middot;</span>
							{formatDate(key.addedAt)}
						</div>
					</div>
					<button
						class="ml-2 px-2 py-1 text-[10px] text-error hover:bg-error/10 rounded transition-colors"
						onclick={() => handleRemove(key.fingerprint)}
					>Remove</button>
				</div>
			{/each}
		</div>
	{/if}

	<!-- Upload / Paste / Quick Import buttons -->
	<div class="flex gap-2">
		<input
			bind:this={fileInput}
			type="file"
			multiple
			onchange={handleFiles}
			class="hidden"
		/>
		<button
			class="flex-1 py-2.5 rounded-lg text-xs font-bold bg-primary/10 text-primary active:bg-primary/20 border border-primary/20 min-h-[40px]"
			onclick={() => fileInput.click()}
			disabled={importing}
		>
			{#if importing}
				<div class="flex items-center justify-center gap-2">
					<div class="w-3 h-3 border border-current border-t-transparent rounded-full spinner"></div>
					Importing...
				</div>
			{:else}
				Upload adbkey File
			{/if}
		</button>
		<button
			class="py-2.5 px-3 rounded-lg text-xs text-muted bg-surface border border-border active:bg-surface-hover min-h-[40px]"
			onclick={() => { showPaste = !showPaste; showQuickImport = false; }}
		>Paste</button>
		<button
			class="py-2.5 px-3 rounded-lg text-xs text-muted bg-surface border border-border active:bg-surface-hover min-h-[40px]"
			onclick={() => { showQuickImport = !showQuickImport; showPaste = false; }}
		>Presets</button>
	</div>

	<!-- Preset keysets (quick import from /keys/) -->
	{#if showQuickImport}
		<div class="space-y-1.5">
			<div class="text-[10px] text-muted">Import a known keyset:</div>
			{#each KNOWN_KEYSETS as keyset}
				{@const alreadyImported = keys.some(k => k.name === keyset.name)}
				<button
					class="w-full flex items-center justify-between p-2.5 rounded-lg border transition-colors min-h-[40px]
						{alreadyImported ? 'bg-success/5 border-success/20 text-muted' : 'bg-background border-border text-foreground hover:border-primary active:bg-primary/5'}"
					onclick={() => handleQuickImport(keyset)}
					disabled={alreadyImported || quickImporting !== null}
				>
					<div class="text-left">
						<div class="text-xs font-medium">{keyset.name}</div>
						<div class="text-[10px] text-muted">{keyset.desc}</div>
					</div>
					{#if alreadyImported}
						<span class="text-[10px] text-success">Imported</span>
					{:else if quickImporting === keyset.file}
						<div class="w-3 h-3 border border-current border-t-transparent rounded-full spinner"></div>
					{:else}
						<span class="text-[10px] text-primary">Import</span>
					{/if}
				</button>
			{/each}
		</div>
	{/if}

	<!-- Paste PEM area -->
	{#if showPaste}
		<div class="space-y-2">
			<textarea
				bind:value={pasteText}
				placeholder="-----BEGIN PRIVATE KEY-----&#10;...&#10;-----END PRIVATE KEY-----"
				rows={6}
				class="w-full px-3 py-2 text-[10px] font-mono rounded-lg bg-background border border-border text-foreground placeholder:text-muted/40 focus:outline-none focus:border-primary resize-none"
			></textarea>
			<div class="flex gap-2">
				<button
					class="flex-1 py-2 rounded-lg text-xs font-bold bg-primary text-background active:bg-primary-dim min-h-[36px]"
					onclick={handlePaste}
					disabled={importing || !pasteText.trim()}
				>Import Key</button>
				<button
					class="py-2 px-3 rounded-lg text-xs text-muted bg-surface border border-border min-h-[36px]"
					onclick={() => { showPaste = false; pasteText = ''; }}
				>Cancel</button>
			</div>
		</div>
	{/if}

	{#if error}
		<div class="text-xs text-error whitespace-pre-line">{error}</div>
	{/if}
	{#if success}
		<div class="text-xs text-success">{success}</div>
	{/if}

	{#if keys.length === 0}
		<div class="p-3 rounded-lg bg-warning/5 border border-warning/20">
			<p class="text-[10px] text-muted leading-relaxed">
				<span class="font-bold text-warning">No keys imported.</span>
				Without a trusted key, phones will reject connections. Upload your adbkey file or use a preset keyset.
			</p>
		</div>
	{/if}

	<div class="p-2.5 rounded-lg bg-surface/50 border border-border/50">
		<p class="text-[10px] text-muted leading-relaxed">
			Keys are encrypted with AES-256-GCM using a browser-only key. They cannot be extracted outside this browser. Multiple keys are tried in order during auth.
		</p>
	</div>
</div>
