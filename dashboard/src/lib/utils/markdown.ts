/**
 * Simple markdown → HTML renderer. No external dependencies.
 * Handles: fenced code blocks, inline code, bold, italic, headings, lists, links, paragraphs.
 * HTML-escapes input first to prevent XSS.
 */

function escapeHtml(text: string): string {
	return text
		.replace(/&/g, '&amp;')
		.replace(/</g, '&lt;')
		.replace(/>/g, '&gt;')
		.replace(/"/g, '&quot;');
}

export function renderMarkdown(text: string): string {
	if (!text) return '';

	// Extract fenced code blocks before escaping
	const codeBlocks: string[] = [];
	const withPlaceholders = text.replace(/```(\w*)\n([\s\S]*?)```/g, (_match, lang, code) => {
		const idx = codeBlocks.length;
		const escaped = escapeHtml(code.replace(/\n$/, ''));
		const langAttr = lang ? ` class="language-${escapeHtml(lang)}"` : '';
		codeBlocks.push(`<pre><code${langAttr}>${escaped}</code></pre>`);
		return `\x00CODEBLOCK${idx}\x00`;
	});

	// Escape remaining HTML
	let html = escapeHtml(withPlaceholders);

	// Restore code blocks
	html = html.replace(/\x00CODEBLOCK(\d+)\x00/g, (_m, idx) => codeBlocks[parseInt(idx)]);

	// Inline code (after escaping so backticks are clean)
	html = html.replace(/`([^`]+)`/g, '<code>$1</code>');

	// Bold and italic
	html = html.replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>');
	html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
	html = html.replace(/\*(.+?)\*/g, '<em>$1</em>');

	// Links: [text](url)
	html = html.replace(
		/\[([^\]]+)\]\(([^)]+)\)/g,
		'<a href="$2" target="_blank" rel="noopener">$1</a>'
	);

	// Process lines
	const lines = html.split('\n');
	const result: string[] = [];
	let inList = false;

	for (let i = 0; i < lines.length; i++) {
		const line = lines[i];

		// Code block passthrough (already rendered)
		if (line.startsWith('<pre>')) {
			if (inList) { result.push('</ul>'); inList = false; }
			// Collect until </pre>
			let block = line;
			let j = i;
			while (!block.includes('</pre>') && j < lines.length - 1) {
				j++;
				block += '\n' + lines[j];
			}
			result.push(block);
			i = j;
			continue;
		}

		// Headings
		const headingMatch = line.match(/^(#{1,3})\s+(.+)/);
		if (headingMatch) {
			if (inList) { result.push('</ul>'); inList = false; }
			const level = headingMatch[1].length;
			result.push(`<h${level}>${headingMatch[2]}</h${level}>`);
			continue;
		}

		// Unordered list
		if (line.match(/^[\-\*]\s+/)) {
			if (!inList) { result.push('<ul>'); inList = true; }
			result.push(`<li>${line.replace(/^[\-\*]\s+/, '')}</li>`);
			continue;
		}

		// Ordered list
		if (line.match(/^\d+\.\s+/)) {
			if (!inList) { result.push('<ul>'); inList = true; }
			result.push(`<li>${line.replace(/^\d+\.\s+/, '')}</li>`);
			continue;
		}

		// End list if we hit a non-list line
		if (inList) { result.push('</ul>'); inList = false; }

		// Empty line
		if (line.trim() === '') {
			continue;
		}

		// Regular paragraph
		result.push(`<p>${line}</p>`);
	}

	if (inList) result.push('</ul>');

	return result.join('\n');
}
