/**
 * Layer Weight Calculator — port of calc_layer_weights() from launch_cellswarm_master.sh.
 *
 * Distributes transformer layers across ring nodes.
 * Rank 0 (master) gets fewer layers because it also runs HTTP server + draft model.
 */

/**
 * Calculate layer distribution for a ring of N phones.
 *
 * @param totalLayers - Total transformer layers in model (e.g. 62 for DeepSeek 33B)
 * @param nPhones - Number of phones in ring
 * @param rank0Reduction - How many fewer layers rank 0 gets (default: 6)
 * @returns Comma-separated layer weights string (e.g. "3,7,7,7,7,7,7,7,7,7,7,8")
 */
export function calcLayerWeights(
	totalLayers: number,
	nPhones: number,
	rank0Reduction = 6
): string {
	if (nPhones <= 0) return '';
	if (nPhones === 1) return String(totalLayers);

	let rank0Layers = Math.floor(totalLayers / nPhones) - rank0Reduction;
	if (rank0Layers < 5) rank0Layers = 5;

	const remaining = totalLayers - rank0Layers;
	const others = nPhones - 1;
	const perOther = Math.floor(remaining / others);
	const leftover = remaining % others;

	const weights: number[] = [rank0Layers];
	for (let i = 0; i < others; i++) {
		const extra = i < leftover ? 1 : 0;
		weights.push(perOther + extra);
	}

	return weights.join(',');
}

/**
 * Parse a layer weights string into an array.
 */
export function parseLayerWeights(lw: string): number[] {
	return lw.split(',').map(s => parseInt(s.trim())).filter(n => !isNaN(n));
}

/**
 * Get layers for a specific rank from a weights string.
 */
export function getLayersForRank(lw: string, rank: number): number {
	const weights = parseLayerWeights(lw);
	return weights[rank] ?? 0;
}

/**
 * Validate layer weights sum matches total layers.
 */
export function validateLayerWeights(lw: string, totalLayers: number): boolean {
	const weights = parseLayerWeights(lw);
	const sum = weights.reduce((a, b) => a + b, 0);
	return sum === totalLayers;
}
