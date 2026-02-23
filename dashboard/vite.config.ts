import tailwindcss from '@tailwindcss/vite';
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

export default defineConfig({
	plugins: [tailwindcss(), sveltekit()],
	// No proxy config — all communication happens via WebUSB + ADB sockets
	// directly in the browser. No backend server required.
});
