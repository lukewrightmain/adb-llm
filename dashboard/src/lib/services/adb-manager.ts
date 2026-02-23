/**
 * ADB Manager — WebUSB lifecycle management.
 *
 * Handles USB device discovery, ADB authentication, connection/disconnection.
 * Uses @yume-chan/adb for the ADB protocol over WebUSB.
 */

import { Adb, AdbDaemonTransport } from '@yume-chan/adb';
import { AdbDaemonWebUsbDeviceManager } from '@yume-chan/adb-daemon-webusb';
import AdbWebCredentialStore from '@yume-chan/adb-credential-web';
import type { ManagedDevice } from '$lib/types/adb';

// Credential store persists RSA keys in IndexedDB so users don't have to
// re-authorize on every page load.
const credentialStore = new AdbWebCredentialStore();

let usbManager: AdbDaemonWebUsbDeviceManager | undefined;

/**
 * Check if WebUSB is available in this browser.
 */
export function isWebUsbSupported(): boolean {
	return typeof navigator !== 'undefined' && 'usb' in navigator;
}

/**
 * Initialize the WebUSB device manager. Call once on app startup.
 */
export function initUsbManager(): AdbDaemonWebUsbDeviceManager | undefined {
	if (!isWebUsbSupported()) return undefined;
	if (!usbManager) {
		usbManager = AdbDaemonWebUsbDeviceManager.BROWSER!;
	}
	return usbManager;
}

/**
 * Prompt the user to select a USB device (triggers browser permission dialog).
 * Returns the raw USB device, or null if cancelled.
 */
export async function requestDevice(): Promise<USBDevice | null> {
	const mgr = initUsbManager();
	if (!mgr) return null;

	try {
		const device = await mgr.requestDevice();
		return device?.raw ?? null;
	} catch (e) {
		// User cancelled the dialog
		if ((e as Error).name === 'NotFoundError') return null;
		throw e;
	}
}

/**
 * Get all previously-paired USB devices (no permission prompt).
 * Useful for auto-reconnect on page reload.
 */
export async function getPairedDevices(): Promise<USBDevice[]> {
	if (!isWebUsbSupported()) return [];
	try {
		return await navigator.usb.getDevices();
	} catch {
		return [];
	}
}

/**
 * Connect to a USB device and establish ADB session.
 * Returns a connected Adb instance.
 */
export async function connectDevice(usbDevice: USBDevice): Promise<Adb> {
	const mgr = initUsbManager();
	if (!mgr) throw new Error('WebUSB not available');

	// Wrap the raw USBDevice in ya-webadb's device abstraction
	const devices = await mgr.getDevices();
	let adbDevice = devices.find(d => d.raw === usbDevice);

	if (!adbDevice) {
		// If getDevices doesn't return it, try the full list
		throw new Error('USB device not found in ADB device list');
	}

	const connection = await adbDevice.connect();

	const transport = await AdbDaemonTransport.authenticate({
		serial: adbDevice.serial,
		connection,
		credentialStore,
	});

	return new Adb(transport);
}

/**
 * Disconnect an ADB session cleanly.
 */
export async function disconnectDevice(adb: Adb): Promise<void> {
	try {
		await adb.close();
	} catch {
		// Already closed or errored — ignore
	}
}

/**
 * Create a ManagedDevice shell from a USB device (before ADB connect).
 */
export function createManagedDevice(usbDevice: USBDevice): ManagedDevice {
	const serial = usbDevice.serialNumber ?? `usb-${usbDevice.vendorId}-${usbDevice.productId}`;
	return {
		serial,
		shortSerial: serial.length > 12 ? serial.slice(-8) : serial,
		adb: null,
		usbDevice,
		state: 'disconnected',
		info: null,
		ringRank: null,
		models: [],
		lastError: null,
		lastProbeAt: 0,
	};
}

/**
 * Listen for USB connect/disconnect events.
 */
export function onUsbConnect(callback: (device: USBDevice) => void): () => void {
	if (!isWebUsbSupported()) return () => {};
	const handler = (e: USBConnectionEvent) => callback(e.device);
	navigator.usb.addEventListener('connect', handler);
	return () => navigator.usb.removeEventListener('connect', handler);
}

export function onUsbDisconnect(callback: (device: USBDevice) => void): () => void {
	if (!isWebUsbSupported()) return () => {};
	const handler = (e: USBConnectionEvent) => callback(e.device);
	navigator.usb.addEventListener('disconnect', handler);
	return () => navigator.usb.removeEventListener('disconnect', handler);
}
