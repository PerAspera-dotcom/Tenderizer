import { useEffect, useState } from 'react';
import { getVaultRules, putVaultRules } from '../../api';

export default function VaultRules() {
  const [hints, setHints] = useState<string[] | null>(null);
  const [draft, setDraft] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    getVaultRules()
      .then(r => setHints(r.hints))
      .catch(() => setError('Failed to load extraction hints'))
      .finally(() => setLoading(false));
  }, []);

  function addHint() {
    const text = draft.trim();
    if (!text || !hints) return;
    setHints([...hints, text]);
    setDraft('');
    setSaved(false);
  }

  function removeHint(i: number) {
    setHints(h => h && h.filter((_, idx) => idx !== i));
    setSaved(false);
  }

  async function handleSave() {
    if (!hints) return;
    setSaving(true);
    setError('');
    try {
      await putVaultRules(hints);
      setSaved(true);
    } catch {
      setError('Failed to save — try again');
    } finally {
      setSaving(false);
    }
  }

  if (loading) return <div className="loading">Loading…</div>;
  if (error && !hints) return <div className="error">{error}</div>;
  if (!hints) return null;

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 6, flexWrap: 'wrap' }}>
        <h1 style={{ fontSize: 28, fontWeight: 700 }}>Metadata Rules</h1>
        <span style={{ background: 'rgba(96,165,250,0.15)', color: '#60a5fa', border: '1px solid rgba(96,165,250,0.3)', borderRadius: 6, fontSize: 11, fontWeight: 700, padding: '3px 8px', letterSpacing: '0.06em' }}>
          VAULT
        </span>
      </div>
      <p style={{ color: '#8a97ac', marginBottom: 20, fontSize: 13 }}>
        Vault extraction is Claude Vision, not a fixed field list — hints below are appended to the
        extraction prompt for every document your tenant ingests, so you can steer it toward fields
        it wouldn't otherwise think to check.
      </p>

      <div className="card" style={{ padding: '20px 24px', maxWidth: 640, display: 'flex', flexDirection: 'column', gap: 16 }}>
        <div>
          <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.1em', color: '#4c5a70', textTransform: 'uppercase', marginBottom: 10 }}>
            Extraction hints
          </div>
          {hints.length === 0 ? (
            <div style={{ color: '#8a97ac', fontSize: 13, marginBottom: 4 }}>
              No custom hints yet — extraction relies on the default prompt only.
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 4 }}>
              {hints.map((hint, i) => (
                <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, background: '#0f1623', border: '1px solid #1a2334', borderRadius: 6, padding: '7px 10px' }}>
                  <span style={{ flex: 1, fontSize: 13, color: '#cdd6e3' }}>{hint}</span>
                  <button className="btn btn-ghost" style={{ fontSize: 11, padding: '2px 8px' }} onClick={() => removeHint(i)}>
                    Remove
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>

        <div style={{ display: 'flex', gap: 8 }}>
          <input
            className="input-field"
            style={{ flex: 1 }}
            placeholder="e.g. always check for EN 13501 fire class if present"
            value={draft}
            onChange={e => setDraft(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') addHint(); }}
          />
          <button className="btn btn-blue" onClick={addHint} disabled={!draft.trim()}>Add</button>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 12, borderTop: '1px solid #1f2b40', paddingTop: 16 }}>
          <button className="btn btn-teal" onClick={handleSave} disabled={saving}>
            {saving ? 'Saving…' : 'Save changes'}
          </button>
          {saved && <span style={{ color: '#34d399', fontSize: 13 }}>Saved</span>}
        </div>
        {error && <div style={{ color: '#f87171', fontSize: 13 }}>{error}</div>}
      </div>
    </div>
  );
}
