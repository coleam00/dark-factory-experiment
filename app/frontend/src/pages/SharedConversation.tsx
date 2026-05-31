import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { CitationModal } from '../components/CitationModal';
import { Message } from '../components/Message';
import { getSharedConversation, type Citation, type SharedConversation as SharedConversationType } from '../lib/api';

export function SharedConversation() {
  const { token } = useParams<{ token: string }>();
  const [data, setData] = useState<SharedConversationType | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedCitation, setSelectedCitation] = useState<Citation | null>(null);

  useEffect(() => {
    if (!token) {
      setError('Invalid share link');
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    getSharedConversation(token)
      .then(setData)
      .catch((e) => {
        setError(e instanceof Error ? e.message : 'Failed to load conversation');
      })
      .finally(() => setLoading(false));
  }, [token]);

  const handleCitationClick = (citation: Citation) => {
    if (citation.source_type === 'dynamous') {
      if (citation.lesson_url) {
        window.open(citation.lesson_url, '_blank', 'noopener,noreferrer');
      }
    } else {
      setSelectedCitation(citation);
    }
  };

  if (loading) {
    return (
      <div
        style={{
          minHeight: '100vh',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: '#0a0a0f',
          color: '#94a3b8',
        }}
      >
        Loading…
      </div>
    );
  }

  if (error || !data) {
    return (
      <div
        style={{
          minHeight: '100vh',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: '#0a0a0f',
          color: '#94a3b8',
          padding: 24,
          textAlign: 'center',
        }}
      >
        <div>
          <h1 style={{ margin: '0 0 8px', fontSize: 20, fontWeight: 600, color: '#f1f5f9' }}>
            This link is no longer available
          </h1>
          <p style={{ margin: 0, maxWidth: 400, lineHeight: 1.6 }}>
            The conversation may have been unshared or the link may be incorrect.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div
      style={{
        minHeight: '100vh',
        background: '#0a0a0f',
        color: '#f1f5f9',
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      {/* Header */}
      <div
        style={{
          borderBottom: '1px solid rgba(255,255,255,0.06)',
          padding: '16px 24px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
        }}
      >
        <h1 style={{ margin: 0, fontSize: 16, fontWeight: 600, color: '#f1f5f9' }}>
          {data.title}
        </h1>
        <span
          style={{
            fontSize: 12,
            color: '#94a3b8',
            background: 'rgba(148,163,184,0.1)',
            padding: '4px 10px',
            borderRadius: 12,
          }}
        >
          Read-only
        </span>
      </div>

      {/* Messages */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '24px 24px 80px' }}>
        {data.messages.map((msg) => (
          <Message
            key={msg.id}
            role={msg.role as 'user' | 'assistant'}
            content={msg.content}
            sources={msg.sources}
            onCitationClick={handleCitationClick}
          />
        ))}
      </div>

      {selectedCitation && (
        <CitationModal citation={selectedCitation} onClose={() => setSelectedCitation(null)} />
      )}
    </div>
  );
}
