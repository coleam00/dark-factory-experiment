import { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import type { Components } from 'react-markdown';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism';
import remarkGfm from 'remark-gfm';

// ── Copy button for code blocks ──────────────────────────────────
function CopyButton({ code }: { code: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Fallback for older browsers
      const el = document.createElement('textarea');
      el.value = code;
      document.body.appendChild(el);
      el.select();
      document.execCommand('copy');
      document.body.removeChild(el);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  return (
    <button onClick={handleCopy} className={`copy-btn${copied ? ' copied' : ''}`}>
      {copied ? 'Copied!' : 'Copy'}
    </button>
  );
}

// ── Code block with header (language label + copy) ────────────────
interface CodeBlockProps {
  language: string | null;
  code: string;
}

function CodeBlock({ language, code }: CodeBlockProps) {
  return (
    <div className="code-block-wrapper">
      <div className="code-block-header">
        <span className="code-lang-label">{language || 'plaintext'}</span>
        <CopyButton code={code} />
      </div>
      {language ? (
        <SyntaxHighlighter
          style={oneDark as Record<string, React.CSSProperties>}
          language={language}
          PreTag="div"
          customStyle={{
            margin: 0,
            borderRadius: 0,
            background: '#0d1117',
            fontSize: 13,
            fontFamily: '"JetBrains Mono", "Fira Code", monospace',
          }}
        >
          {code}
        </SyntaxHighlighter>
      ) : (
        <pre
          style={{
            margin: 0,
            padding: '12px 16px',
            background: '#0d1117',
            fontSize: 13,
            fontFamily: '"JetBrains Mono", "Fira Code", monospace',
            overflowX: 'auto',
            color: '#f1f5f9',
          }}
        >
          <code>{code}</code>
        </pre>
      )}
    </div>
  );
}

// ── Markdown renderer ─────────────────────────────────────────────
interface MarkdownRendererProps {
  content: string;
}

export function MarkdownRenderer({ content }: MarkdownRendererProps) {
  const components: Components = {
    code({ className, children, ...props }) {
      const match = /language-(\w+)/.exec(className || '');
      const codeStr = String(children).replace(/\n$/, '');

      // react-markdown v9 passes `node` — detect block vs inline by presence
      // of a language class or multi-line content
      const isBlock = Boolean(match) || codeStr.includes('\n');

      if (isBlock) {
        return <CodeBlock language={match ? match[1] : null} code={codeStr} />;
      }

      return (
        <code
          className={className}
          style={{
            fontFamily: '"JetBrains Mono", "Fira Code", monospace',
            fontSize: 13,
            background: 'rgba(255,255,255,0.08)',
            padding: '2px 5px',
            borderRadius: 4,
          }}
          {...props}
        >
          {children}
        </code>
      );
    },
  };

  return (
    <div className="assistant-content">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {content}
      </ReactMarkdown>
    </div>
  );
}
