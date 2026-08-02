// Minimal markdown rendering for LLM replies -- just **bold** and "- " bullet
// lists, which is all the chat system prompts are instructed to use. Not a
// full markdown parser on purpose: keeps the bundle small and avoids a new
// dependency for two formatting rules.
function renderInline(text, keyPrefix) {
  const parts = text.split(/\*\*(.+?)\*\*/g);
  return parts.map((part, i) => (i % 2 === 1 ? <strong key={`${keyPrefix}-${i}`}>{part}</strong> : part));
}

export default function FormattedText({ text }) {
  if (!text) return null;
  const lines = text.split('\n');
  const blocks = [];
  let currentList = null;
  lines.forEach((line) => {
    const bulletMatch = /^\s*[-*]\s+(.*)/.exec(line);
    if (bulletMatch) {
      if (!currentList) { currentList = []; blocks.push(currentList); }
      currentList.push(bulletMatch[1]);
    } else {
      currentList = null;
      blocks.push(line);
    }
  });
  return blocks.map((block, i) =>
    Array.isArray(block) ? (
      <ul className="msg-list" key={i}>
        {block.map((item, j) => <li key={j}>{renderInline(item, `${i}-${j}`)}</li>)}
      </ul>
    ) : block === '' ? (
      <br key={i} />
    ) : (
      <div className="msg-line" key={i}>{renderInline(block, `${i}`)}</div>
    )
  );
}
