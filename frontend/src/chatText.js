// Minimal, safe formatting for assistant replies (Prompt 18): **bold**, "- " bullet lines and paragraphs.
// The text is HTML-escaped FIRST, so nothing the model writes can inject markup; only these three patterns
// become tags afterwards.
const escape = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;').replace(/'/g, '&#39;')
const inline = (s) => escape(s).replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')

export function formatReply(text) {
  const out = []
  let list = null
  for (const line of (text ?? '').split('\n')) {
    const bullet = line.match(/^\s*[-*•]\s+(.*)$/)
    if (bullet) {
      if (!list) { list = []; out.push(list) }
      list.push(inline(bullet[1]))
      continue
    }
    list = null
    if (line.trim()) out.push(`<p>${inline(line)}</p>`)
  }
  return out.map((b) => (Array.isArray(b) ? `<ul>${b.map((li) => `<li>${li}</li>`).join('')}</ul>` : b)).join('')
}
