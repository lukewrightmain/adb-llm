<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import * as d3 from 'd3';
	import { getRingStatus, getDevices } from '$lib/stores/app.svelte';
	import type { RingNode, Device } from '$lib/types';

	interface Props {
		class?: string;
		compact?: boolean;
	}

	let { class: className = '', compact = false }: Props = $props();

	let svgContainer: SVGSVGElement | undefined = $state();
	let resizeObserver: ResizeObserver | undefined;

	const ring = $derived(getRingStatus());
	const allDevices = $derived(getDevices());

	function getDeviceForNode(node: RingNode): Device | undefined {
		if (node.is_host) return undefined;
		return allDevices.find((d) => d.serial === node.serial);
	}

	function thermalColor(temp: number | null): string {
		if (temp === null) return '#8b949e';
		if (temp <= 35) return '#60a5fa';
		if (temp <= 42) return '#3fb950';
		if (temp <= 50) return '#d29922';
		return '#f85149';
	}

	function nodeColor(node: RingNode): string {
		if (!node.running) return '#30363d';
		if (node.is_host) return '#60a5fa';
		return '#3fb950';
	}

	function renderGraph() {
		if (!svgContainer) return;

		d3.select(svgContainer).selectAll('*').remove();

		const rect = svgContainer.getBoundingClientRect();
		const width = rect.width;
		const height = rect.height;
		if (width === 0 || height === 0) return;

		const centerX = width / 2;
		const centerY = height / 2;
		const radius = Math.min(centerX, centerY) - (compact ? 30 : 60);

		const svg = d3.select(svgContainer);
		const nodes = ring.nodes;

		if (nodes.length === 0) {
			svg.append('text')
				.attr('x', centerX)
				.attr('y', centerY)
				.attr('text-anchor', 'middle')
				.attr('fill', '#8b949e')
				.attr('font-size', compact ? '11px' : '14px')
				.attr('font-family', 'var(--font-mono)')
				.text('No ring active');
			return;
		}

		// Defs
		const defs = svg.append('defs');

		// Glow filter
		const glow = defs.append('filter').attr('id', 'glow').attr('x', '-50%').attr('y', '-50%').attr('width', '200%').attr('height', '200%');
		glow.append('feGaussianBlur').attr('stdDeviation', '3').attr('result', 'blur');
		const merge = glow.append('feMerge');
		merge.append('feMergeNode').attr('in', 'blur');
		merge.append('feMergeNode').attr('in', 'SourceGraphic');

		// Arrow marker
		defs.append('marker')
			.attr('id', 'arrowhead')
			.attr('viewBox', '0 0 10 7')
			.attr('refX', 10)
			.attr('refY', 3.5)
			.attr('markerWidth', 8)
			.attr('markerHeight', 6)
			.attr('orient', 'auto')
			.append('polygon')
			.attr('points', '0 0, 10 3.5, 0 7')
			.attr('fill', '#60a5fa');

		// Position nodes in circle — rank 0 at top
		const nodePositions = nodes.map((n, i) => {
			const angle = (i / nodes.length) * 2 * Math.PI - Math.PI / 2;
			return {
				...n,
				x: centerX + radius * Math.cos(angle),
				y: centerY + radius * Math.sin(angle),
			};
		});

		// Draw edges (ring connections)
		for (let i = 0; i < nodePositions.length; i++) {
			const src = nodePositions[i];
			const dst = nodePositions[(i + 1) % nodePositions.length];
			const active = src.running && dst.running;

			svg.append('line')
				.attr('x1', src.x)
				.attr('y1', src.y)
				.attr('x2', dst.x)
				.attr('y2', dst.y)
				.attr('class', active ? 'graph-link-active' : 'graph-link')
				.attr('marker-end', active ? 'url(#arrowhead)' : '');
		}

		// Draw nodes
		const nodeSize = compact ? 20 : 30;

		for (const np of nodePositions) {
			const g = svg.append('g').attr('transform', `translate(${np.x}, ${np.y})`);
			const color = nodeColor(np);
			const dev = getDeviceForNode(np);

			if (np.is_host) {
				// Host: diamond shape
				g.append('rect')
					.attr('x', -nodeSize * 0.7)
					.attr('y', -nodeSize * 0.7)
					.attr('width', nodeSize * 1.4)
					.attr('height', nodeSize * 1.4)
					.attr('rx', 4)
					.attr('fill', color)
					.attr('fill-opacity', 0.15)
					.attr('stroke', color)
					.attr('stroke-width', 2)
					.attr('filter', np.running ? 'url(#glow)' : '');

				g.append('text')
					.attr('text-anchor', 'middle')
					.attr('dy', compact ? '0.35em' : '-0.1em')
					.attr('fill', '#60a5fa')
					.attr('font-size', compact ? '9px' : '11px')
					.attr('font-family', 'var(--font-mono)')
					.text('HOST');

				if (!compact) {
					g.append('text')
						.attr('text-anchor', 'middle')
						.attr('dy', '1.2em')
						.attr('fill', '#8b949e')
						.attr('font-size', '9px')
						.attr('font-family', 'var(--font-mono)')
						.text('R0');
				}
			} else {
				// Phone: rounded rectangle
				const w = compact ? 50 : 80;
				const h = compact ? 28 : 44;

				g.append('rect')
					.attr('x', -w / 2)
					.attr('y', -h / 2)
					.attr('width', w)
					.attr('height', h)
					.attr('rx', 6)
					.attr('fill', color)
					.attr('fill-opacity', 0.1)
					.attr('stroke', color)
					.attr('stroke-width', 1.5)
					.attr('filter', np.running ? 'url(#glow)' : '');

				// Rank label
				g.append('text')
					.attr('text-anchor', 'middle')
					.attr('dy', compact ? '0.35em' : '-0.4em')
					.attr('fill', '#e6edf3')
					.attr('font-size', compact ? '9px' : '11px')
					.attr('font-family', 'var(--font-mono)')
					.attr('font-weight', 'bold')
					.text(`R${np.rank}`);

				if (!compact && dev) {
					// RAM bar inside node
					const barW = w - 12;
					const barH = 4;
					const barY = h / 2 - 10;
					const ramPct = dev.total_ram_mb
						? (dev.total_ram_mb - dev.available_ram_mb) / dev.total_ram_mb
						: 0;

					g.append('rect')
						.attr('x', -barW / 2)
						.attr('y', barY)
						.attr('width', barW)
						.attr('height', barH)
						.attr('rx', 2)
						.attr('fill', '#30363d');

					g.append('rect')
						.attr('x', -barW / 2)
						.attr('y', barY)
						.attr('width', barW * ramPct)
						.attr('height', barH)
						.attr('rx', 2)
						.attr('fill', thermalColor(dev.thermal_temp_c));

					// Thermal under node
					g.append('text')
						.attr('text-anchor', 'middle')
						.attr('dy', h / 2 + 12)
						.attr('fill', thermalColor(dev.thermal_temp_c))
						.attr('font-size', '9px')
						.attr('font-family', 'var(--font-mono)')
						.text(`${dev.thermal_temp_c.toFixed(0)}\u00B0C`);
				}
			}
		}
	}

	$effect(() => {
		// Re-render when ring status or devices change
		ring;
		allDevices;
		renderGraph();
	});

	onMount(() => {
		if (svgContainer) {
			resizeObserver = new ResizeObserver(() => renderGraph());
			resizeObserver.observe(svgContainer);
		}
	});

	onDestroy(() => {
		resizeObserver?.disconnect();
	});
</script>

<svg bind:this={svgContainer} class="w-full h-full {className}"></svg>
