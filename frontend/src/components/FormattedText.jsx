import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import '../styles/markdown.css';

// Markdown rendering for LLM replies. The models routinely produce more
// than the **bold** + "- " bullets the system prompts ask for -- headings,
// numbered lists, tables, links, inline code -- and the old two-rule
// renderer showed all of those as raw symbols. react-markdown never renders
// raw HTML from the text (no rehype-raw), so model output can't inject
// markup; remark-gfm adds tables, strikethrough and autolinks.
const COMPONENTS = {
  // Links always leave the app -- a reply is never a place to navigate
  // the SPA itself away from the chat.
  a: ({ node, ...props }) => <a {...props} target="_blank" rel="noopener noreferrer" />,
  // Wide tables scroll inside the bubble instead of stretching it.
  table: ({ node, ...props }) => (
    <div className="md-table-wrap"><table {...props} /></div>
  ),
  // Models sometimes emit a top-level "# Title" -- render every heading at
  // a chat-appropriate size rather than page-title size.
  h1: ({ node, ...props }) => <h4 {...props} />,
  h2: ({ node, ...props }) => <h4 {...props} />,
  h3: ({ node, ...props }) => <h5 {...props} />,
  h4: ({ node, ...props }) => <h5 {...props} />,
  img: () => null, // never load remote images an LLM decided to embed
};

export default function FormattedText({ text }) {
  if (!text) return null;
  return (
    <div className="md">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={COMPONENTS}>
        {text}
      </ReactMarkdown>
    </div>
  );
}
