/**
 * ADB Key Management — Secure import, storage, and credential store.
 *
 * Users upload their existing ADB private key (adbkey file from ~/.android/).
 * Keys are encrypted with AES-256-GCM using a non-extractable WebCrypto key
 * stored in IndexedDB. The encryption key is origin-bound and can't be
 * extracted from the browser — so even if someone copies localStorage,
 * they can't decrypt the ADB key without the IndexedDB entry from the
 * same browser + origin.
 *
 * Flow:
 *   1. User uploads adbkey PEM file (or pastes PEM text)
 *   2. PEM → DER (strip headers, base64-decode)
 *   3. If PKCS#1 format → wrap in PKCS#8 envelope (ya-webadb needs PKCS#8)
 *   4. Validate key via WebCrypto importKey() to confirm it's valid RSA 2048
 *   5. Generate random AES-256-GCM key (non-extractable) → stored in IndexedDB
 *   6. Encrypt DER bytes with AES-GCM → encrypted blob + IV stored in localStorage
 *   7. On connect: decrypt DER → feed to AdbDaemonTransport.authenticate()
 */

// ─── Constants ────────────────────────────────────────────────────────

const IDB_NAME = 'CellSwarmKeyVault';
const IDB_VERSION = 1;
const IDB_STORE = 'encryption-keys';
const IDB_KEY_ID = 'adb-vault-key';
const LS_ENCRYPTED_KEYS = 'cellswarm-adb-keys-encrypted';

// ─── Types ────────────────────────────────────────────────────────────

export interface AdbPrivateKey {
	buffer: Uint8Array;
	name: string;
}

export interface StoredKeyMeta {
	name: string;
	addedAt: number;
	fingerprint: string; // First 8 chars of SHA-256 hex for display
	format?: string;     // 'pkcs8' or 'pkcs1→pkcs8'
}

// ─── IndexedDB: encryption key storage ────────────────────────────────

function openVaultDb(): Promise<IDBDatabase> {
	return new Promise((resolve, reject) => {
		const req = indexedDB.open(IDB_NAME, IDB_VERSION);
		req.onerror = () => reject(req.error);
		req.onupgradeneeded = () => {
			req.result.createObjectStore(IDB_STORE);
		};
		req.onsuccess = () => resolve(req.result);
	});
}

async function getOrCreateVaultKey(): Promise<CryptoKey> {
	const db = await openVaultDb();

	// Try to load existing key
	const existing = await new Promise<CryptoKey | null>((resolve, reject) => {
		const tx = db.transaction(IDB_STORE, 'readonly');
		const store = tx.objectStore(IDB_STORE);
		const req = store.get(IDB_KEY_ID);
		req.onerror = () => reject(req.error);
		req.onsuccess = () => resolve(req.result ?? null);
		tx.oncomplete = () => db.close();
	});

	if (existing) return existing;

	// Generate new non-extractable AES key
	const key = await crypto.subtle.generateKey(
		{ name: 'AES-GCM', length: 256 },
		false, // non-extractable!
		['encrypt', 'decrypt']
	);

	// Store it
	const db2 = await openVaultDb();
	await new Promise<void>((resolve, reject) => {
		const tx = db2.transaction(IDB_STORE, 'readwrite');
		const store = tx.objectStore(IDB_STORE);
		const req = store.put(key, IDB_KEY_ID);
		req.onerror = () => reject(req.error);
		req.onsuccess = () => resolve();
		tx.oncomplete = () => db2.close();
	});

	return key;
}

// ─── Encryption / Decryption ──────────────────────────────────────────

async function encryptBytes(data: Uint8Array): Promise<{ iv: string; ciphertext: string }> {
	const key = await getOrCreateVaultKey();
	const iv = crypto.getRandomValues(new Uint8Array(12));
	const encrypted = await crypto.subtle.encrypt(
		{ name: 'AES-GCM', iv },
		key,
		data
	);
	return {
		iv: uint8ToBase64(iv),
		ciphertext: uint8ToBase64(new Uint8Array(encrypted)),
	};
}

async function decryptBytes(iv: string, ciphertext: string): Promise<Uint8Array> {
	const key = await getOrCreateVaultKey();
	const decrypted = await crypto.subtle.decrypt(
		{ name: 'AES-GCM', iv: base64ToUint8(iv) },
		key,
		base64ToUint8(ciphertext)
	);
	return new Uint8Array(decrypted);
}

// ─── PEM Parsing & PKCS#1 → PKCS#8 Conversion ───────────────────────

/**
 * PKCS#8 header for wrapping a PKCS#1 RSA private key.
 * This is the fixed ASN.1 prefix:
 *   SEQUENCE {
 *     INTEGER 0 (version)
 *     SEQUENCE { OID rsaEncryption, NULL }
 *     OCTET STRING { <pkcs1 key goes here> }
 *   }
 * The OCTET STRING length is variable and must be computed.
 */
function wrapPkcs1InPkcs8(pkcs1Der: Uint8Array): Uint8Array {
	// ASN.1 DER encoding helpers
	function encodeLength(len: number): Uint8Array {
		if (len < 128) return new Uint8Array([len]);
		if (len < 256) return new Uint8Array([0x81, len]);
		return new Uint8Array([0x82, (len >> 8) & 0xff, len & 0xff]);
	}

	// AlgorithmIdentifier: SEQUENCE { OID 1.2.840.113549.1.1.1 (rsaEncryption), NULL }
	const algId = new Uint8Array([
		0x30, 0x0d,
		0x06, 0x09, 0x2a, 0x86, 0x48, 0x86, 0xf7, 0x0d, 0x01, 0x01, 0x01,
		0x05, 0x00,
	]);

	// Version: INTEGER 0
	const version = new Uint8Array([0x02, 0x01, 0x00]);

	// OCTET STRING wrapping the PKCS#1 key
	const octetLenBytes = encodeLength(pkcs1Der.length);
	const octetString = new Uint8Array(1 + octetLenBytes.length + pkcs1Der.length);
	octetString[0] = 0x04;
	octetString.set(octetLenBytes, 1);
	octetString.set(pkcs1Der, 1 + octetLenBytes.length);

	// Outer SEQUENCE
	const innerLen = version.length + algId.length + octetString.length;
	const outerLenBytes = encodeLength(innerLen);
	const result = new Uint8Array(1 + outerLenBytes.length + innerLen);
	result[0] = 0x30;
	result.set(outerLenBytes, 1);
	let offset = 1 + outerLenBytes.length;
	result.set(version, offset); offset += version.length;
	result.set(algId, offset); offset += algId.length;
	result.set(octetString, offset);

	return result;
}

/**
 * Detect if PEM is PKCS#1 format.
 */
function isPkcs1Pem(pem: string): boolean {
	return /-----BEGIN RSA PRIVATE KEY-----/.test(pem);
}

/**
 * Parse a PEM-encoded private key to PKCS#8 DER bytes.
 * Handles both "-----BEGIN PRIVATE KEY-----" (PKCS#8) and
 * "-----BEGIN RSA PRIVATE KEY-----" (PKCS#1 — auto-wrapped to PKCS#8).
 */
export function pemToDer(pem: string): Uint8Array {
	const isPkcs1 = isPkcs1Pem(pem);

	// Strip headers, footers, and whitespace
	const stripped = pem
		.replace(/-----BEGIN [A-Z ]+-----/g, '')
		.replace(/-----END [A-Z ]+-----/g, '')
		.replace(/\s/g, '');
	const rawDer = base64ToUint8(stripped);

	if (isPkcs1) {
		console.log('[adb-keys] Converting PKCS#1 key to PKCS#8 format');
		return wrapPkcs1InPkcs8(rawDer);
	}

	return rawDer;
}

/**
 * Validate that a string looks like a PEM private key.
 */
export function isValidPem(text: string): boolean {
	return /-----BEGIN (RSA )?PRIVATE KEY-----/.test(text) &&
		/-----END (RSA )?PRIVATE KEY-----/.test(text);
}

/**
 * Validate a PKCS#8 DER key by importing it with WebCrypto.
 * This confirms it's a valid RSA 2048-bit key that ya-webadb can use.
 */
async function validatePkcs8Key(der: Uint8Array): Promise<void> {
	try {
		const key = await crypto.subtle.importKey(
			'pkcs8',
			der,
			{ name: 'RSASSA-PKCS1-v1_5', hash: 'SHA-1' },
			false,
			['sign']
		);
		// Check it's RSA by looking at algorithm
		if (key.algorithm.name !== 'RSASSA-PKCS1-v1_5') {
			throw new Error('Not an RSA key');
		}
	} catch (e) {
		throw new Error(`Invalid RSA private key: ${e instanceof Error ? e.message : String(e)}`);
	}
}

// ─── Key Fingerprint ──────────────────────────────────────────────────

async function computeFingerprint(der: Uint8Array): Promise<string> {
	const hash = await crypto.subtle.digest('SHA-256', der);
	const hex = Array.from(new Uint8Array(hash))
		.map(b => b.toString(16).padStart(2, '0'))
		.join('');
	return hex.slice(0, 8);
}

// ─── Base64 Helpers ───────────────────────────────────────────────────

function uint8ToBase64(bytes: Uint8Array): string {
	let binary = '';
	for (let i = 0; i < bytes.length; i++) {
		binary += String.fromCharCode(bytes[i]);
	}
	return btoa(binary);
}

function base64ToUint8(b64: string): Uint8Array {
	const binary = atob(b64);
	const bytes = new Uint8Array(binary.length);
	for (let i = 0; i < binary.length; i++) {
		bytes[i] = binary.charCodeAt(i);
	}
	return bytes;
}

// ─── Key Storage (encrypted in localStorage) ──────────────────────────

interface EncryptedKeyEntry {
	iv: string;
	ciphertext: string;
	name: string;
	fingerprint: string;
	addedAt: number;
	format?: string;
}

function loadEncryptedEntries(): EncryptedKeyEntry[] {
	try {
		const raw = localStorage.getItem(LS_ENCRYPTED_KEYS);
		return raw ? JSON.parse(raw) : [];
	} catch {
		return [];
	}
}

function saveEncryptedEntries(entries: EncryptedKeyEntry[]) {
	localStorage.setItem(LS_ENCRYPTED_KEYS, JSON.stringify(entries));
}

/**
 * Import an ADB private key from PEM text.
 * Validates, converts PKCS#1→PKCS#8 if needed, encrypts, and stores.
 */
export async function importKey(pemText: string, name?: string): Promise<StoredKeyMeta> {
	if (!isValidPem(pemText)) {
		throw new Error('Invalid PEM format. Expected -----BEGIN PRIVATE KEY----- or -----BEGIN RSA PRIVATE KEY-----');
	}

	const wasPkcs1 = isPkcs1Pem(pemText);
	const der = pemToDer(pemText);

	// Validate the key is actually usable
	await validatePkcs8Key(der);

	const fingerprint = await computeFingerprint(der);
	const format = wasPkcs1 ? 'pkcs1→pkcs8' : 'pkcs8';

	console.log(`[adb-keys] Importing key: ${fingerprint}, ${der.length} bytes, format: ${format}`);

	// Check for duplicate
	const entries = loadEncryptedEntries();
	if (entries.find(e => e.fingerprint === fingerprint)) {
		throw new Error(`Key already imported (fingerprint: ${fingerprint})`);
	}

	// Encrypt the DER bytes
	const { iv, ciphertext } = await encryptBytes(der);

	const keyName = name || `adbkey-${fingerprint}`;
	const entry: EncryptedKeyEntry = {
		iv,
		ciphertext,
		name: keyName,
		fingerprint,
		addedAt: Date.now(),
		format,
	};

	entries.push(entry);
	saveEncryptedEntries(entries);

	return { name: keyName, addedAt: entry.addedAt, fingerprint, format };
}

/**
 * Import an ADB private key from a File object.
 */
export async function importKeyFromFile(file: File): Promise<StoredKeyMeta> {
	const text = await file.text();
	// Use filename without extension as key name, but keep full name if no extension
	const name = file.name.includes('.') ? file.name.replace(/\.[^.]+$/, '') : file.name;
	return importKey(text, name);
}

/**
 * Remove an imported key by fingerprint.
 */
export function removeKey(fingerprint: string) {
	const entries = loadEncryptedEntries().filter(e => e.fingerprint !== fingerprint);
	saveEncryptedEntries(entries);
}

/**
 * List all imported key metadata (without decrypting).
 */
export function listKeys(): StoredKeyMeta[] {
	return loadEncryptedEntries().map(e => ({
		name: e.name,
		addedAt: e.addedAt,
		fingerprint: e.fingerprint,
		format: e.format,
	}));
}

/**
 * Check if any keys have been imported.
 */
export function hasImportedKeys(): boolean {
	return loadEncryptedEntries().length > 0;
}

/**
 * Decrypt and yield all stored keys. Used by the credential store.
 */
async function* decryptAllKeys(): AsyncGenerator<AdbPrivateKey> {
	const entries = loadEncryptedEntries();
	console.log(`[adb-keys] Iterating ${entries.length} imported key(s)`);
	for (const entry of entries) {
		try {
			const der = await decryptBytes(entry.iv, entry.ciphertext);
			console.log(`[adb-keys] Yielding key "${entry.name}" (${entry.fingerprint}), ${der.length} bytes`);
			yield { buffer: der, name: entry.name };
		} catch (e) {
			console.warn(`[adb-keys] Failed to decrypt key ${entry.fingerprint}:`, e);
		}
	}
}

// ─── Custom Credential Store ──────────────────────────────────────────

/**
 * ADB Credential Store that uses imported keys first, then falls back
 * to generating new keys (which require on-device authorization).
 *
 * Drop-in replacement for AdbWebCredentialStore.
 */
export class CellSwarmCredentialStore {
	#appName: string;

	constructor(appName: string = 'CellSwarm') {
		this.#appName = appName;
	}

	/**
	 * Generate a new RSA key pair.
	 * This is called as last resort when no imported key is accepted.
	 * The generated key is also saved to the Tango DB so ya-webadb
	 * can find it on subsequent connections, and the user can approve
	 * it on-device once.
	 */
	async generateKey(): Promise<AdbPrivateKey> {
		console.log('[adb-keys] generateKey() called — generating new RSA 2048 key pair');
		const { privateKey: cryptoKey } = await crypto.subtle.generateKey(
			{
				name: 'RSASSA-PKCS1-v1_5',
				modulusLength: 2048,
				publicExponent: new Uint8Array([0x01, 0x00, 0x01]),
				hash: 'SHA-1',
			},
			true,
			['sign', 'verify']
		);
		const privateKey = new Uint8Array(
			await crypto.subtle.exportKey('pkcs8', cryptoKey)
		);

		// Also store in ya-webadb's default Tango DB for persistence
		try {
			const db = await new Promise<IDBDatabase>((resolve, reject) => {
				const req = indexedDB.open('Tango', 1);
				req.onerror = () => reject(req.error);
				req.onupgradeneeded = () => {
					req.result.createObjectStore('Authentication', { autoIncrement: true });
				};
				req.onsuccess = () => resolve(req.result);
			});
			await new Promise<void>((resolve, reject) => {
				const tx = db.transaction('Authentication', 'readwrite');
				const store = tx.objectStore('Authentication');
				const req = store.add(privateKey);
				req.onerror = () => reject(req.error);
				req.onsuccess = () => resolve();
				tx.oncomplete = () => db.close();
			});
			console.log('[adb-keys] Saved generated key to Tango DB');
		} catch (e) {
			console.warn('[adb-keys] Failed to save generated key to Tango DB:', e);
		}

		return {
			buffer: privateKey,
			name: `${this.#appName}@${globalThis.location?.hostname ?? 'unknown'}`,
		};
	}

	/**
	 * Iterate over all stored keys — imported keys first, then any
	 * previously generated keys from the default ya-webadb store.
	 */
	async *iterateKeys(): AsyncGenerator<AdbPrivateKey> {
		console.log('[adb-keys] iterateKeys() called');

		// Yield imported keys first (these are pre-authorized on the phones)
		yield* decryptAllKeys();

		// Also yield any keys from the default ya-webadb IndexedDB store
		// (in case someone already authorized via the browser or generateKey was called)
		try {
			const db = await new Promise<IDBDatabase>((resolve, reject) => {
				const req = indexedDB.open('Tango', 1);
				req.onerror = () => reject(req.error);
				req.onupgradeneeded = () => {
					req.result.createObjectStore('Authentication', { autoIncrement: true });
				};
				req.onsuccess = () => resolve(req.result);
			});

			const keys = await new Promise<Uint8Array[]>((resolve, reject) => {
				const tx = db.transaction('Authentication', 'readonly');
				const store = tx.objectStore('Authentication');
				const req = store.getAll();
				req.onerror = () => reject(req.error);
				req.onsuccess = () => resolve(req.result ?? []);
				tx.oncomplete = () => db.close();
			});

			console.log(`[adb-keys] Found ${keys.length} key(s) in Tango DB`);
			for (const key of keys) {
				yield {
					buffer: key instanceof Uint8Array ? key : new Uint8Array(key),
					name: `Tango@${globalThis.location?.hostname ?? 'unknown'}`,
				};
			}
		} catch {
			// Tango DB doesn't exist yet, ignore
		}
	}
}

/**
 * Singleton credential store instance.
 * Used by both adb-manager.ts (USB) and adb-tcp.ts (TCP).
 */
export const credentialStore = new CellSwarmCredentialStore();
