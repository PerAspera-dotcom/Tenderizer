import { useEffect, useState } from 'react';
import { getDedupSettings, putDedupSettings } from '../../api';
import type { DedupSettings } from '../../types';

// CR-007 Phase C (C1b): the only tunable in cross-portal dedup — signal 1
// (buyer-reference match) is exact, no threshold; this controls signal 2
// (translated-description similarity) only.
export default function DedupConfig() {
  const [data, setData] = useState<DedupSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    getDedupSettings()
      .then(setData)
      .catch(() => setError('Failed to load dedup settings'))
      .finally(() => setLoading(false));
  }, []);

  async function handleSave() {
    if (!data) return;
    setSaving(true);
    setError('');
    try {
      await putDedupSettings({ similarity_threshold: data.similarity_threshold });
      setSaved(true);
    } catch {
      setError('Failed to save — try again');
    } finally {
      setSaving(false);
    }
  }

  if (loading) return <div className="loading">Loading…</div>;
  if (error && !data) return <div className="error">{error}</div>;
  if (!data) return null;

  return (
    <div className="card" style={{ padding: '20px 24px', maxWidth: 480, display: 'flex', flexDirection: 'column', gap: 20 }}>
      <div>
        <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.1em', color: '#4c5a70', textTransform: 'uppercase', marginBottom: 10 }}>
          Cross-portal similarity threshold
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <input
            type="range"
            min={0} max={1} step={0.05}
            value={data.similarity_threshold}
            onChange={e => { setData({ ...data, similarity_threshold: Number(e.target.value) }); setSaved(false); }}
            style={{ flex: 1 }}
          />
          <span className="mono" style={{ fontSize: 13, color: '#cdd6e3', width: 40 }}>
            {Math.round(data.similarity_threshold * 100)}%
          </span>
        </div>
        <p style={{ color: '#8a97ac', fontSize: 12, marginTop: 8 }}>
          When a TED and a national-portal (e.g. BOAMP) notice have no shared buyer reference number,
          their translated descriptions are compared instead — above this similarity they're flagged
          as "also listed on" each other in the Review Queue and Tender Feed. Lower catches more
          duplicates but risks false positives; higher is stricter.
        </p>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <button className="btn btn-teal" onClick={handleSave} disabled={saving}>
          {saving ? 'Saving…' : 'Save changes'}
        </button>
        {saved && <span style={{ color: '#34d399', fontSize: 13 }}>Saved</span>}
      </div>
      {error && <div style={{ color: '#f87171', fontSize: 13 }}>{error}</div>}
    </div>
  );
}
