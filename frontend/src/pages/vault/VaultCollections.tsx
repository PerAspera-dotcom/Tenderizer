import { useEffect, useState } from 'react';
import { useNavigate } from '../../router';
import { listVaultTags } from '../../api';

export default function VaultCollections() {
  const navigate = useNavigate();
  const [tags, setTags] = useState<string[] | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    listVaultTags()
      .then(r => setTags(r.tags))
      .catch(() => setError('Failed to load tags'));
  }, []);

  if (error) return <div className="error">{error}</div>;
  if (!tags) return <div className="loading">Loading…</div>;

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 6, flexWrap: 'wrap' }}>
        <h1 style={{ fontSize: 28, fontWeight: 700 }}>Tags</h1>
        <span style={{ background: 'rgba(96,165,250,0.15)', color: '#60a5fa', border: '1px solid rgba(96,165,250,0.3)', borderRadius: 6, fontSize: 11, fontWeight: 700, padding: '3px 8px', letterSpacing: '0.06em' }}>
          VAULT
        </span>
      </div>
      <p style={{ color: '#8a97ac', marginBottom: 20, fontSize: 13 }}>
        Every tag currently in use across your document library — assign tags from a document's
        detail panel in the Library, then jump back here to browse by tag.
      </p>

      <div className="card" style={{ padding: 20 }}>
        {tags.length === 0 ? (
          <div style={{ color: '#8a97ac', fontSize: 13, textAlign: 'center', padding: 20 }}>
            No tags yet — tag a document in the Library to get started.
          </div>
        ) : (
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {tags.map(tag => (
              <button
                key={tag}
                className="mono"
                onClick={() => navigate(`/vault/library?tag=${encodeURIComponent(tag)}`)}
                style={{
                  background: 'rgba(96,165,250,0.08)', color: '#9cc1fb',
                  border: '1px solid rgba(96,165,250,0.2)', borderRadius: 6,
                  fontSize: 12, padding: '5px 12px', cursor: 'pointer',
                }}
              >
                {tag}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
