import { useEffect, useState } from 'react';
import { getComposerSettings, putComposerSettings } from '../../api';
import type { ComposerSettings as ComposerSettingsType } from '../../types';

export default function ComposerSettings() {
  const [data, setData] = useState<ComposerSettingsType | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    getComposerSettings()
      .then(setData)
      .catch(() => setError('Failed to load Composer settings'))
      .finally(() => setLoading(false));
  }, []);

  function update<K extends keyof ComposerSettingsType>(key: K, value: ComposerSettingsType[K]) {
    setData(d => d && { ...d, [key]: value });
    setSaved(false);
  }

  async function handleSave() {
    if (!data) return;
    setSaving(true);
    setError('');
    try {
      await putComposerSettings({
        good_similarity: data.good_similarity,
        partial_similarity: data.partial_similarity,
        top_k: data.top_k,
      });
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
    <div>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 6, flexWrap: 'wrap' }}>
        <h1 style={{ fontSize: 28, fontWeight: 700 }}>Composer Settings</h1>
        <span style={{ background: 'rgba(192,132,252,0.15)', color: '#c084fc', border: '1px solid rgba(192,132,252,0.3)', borderRadius: 6, fontSize: 11, fontWeight: 700, padding: '3px 8px', letterSpacing: '0.06em' }}>
          COMPOSER
        </span>
      </div>
      <p style={{ color: '#8a97ac', marginBottom: 20, fontSize: 13 }}>
        Retrieval model and similarity thresholds used when generating a draft
      </p>

      <div className="card" style={{ padding: '20px 24px', maxWidth: 480, display: 'flex', flexDirection: 'column', gap: 20 }}>
        <div>
          <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.1em', color: '#4c5a70', textTransform: 'uppercase', marginBottom: 10 }}>
            Model
          </div>
          <span className="mono" style={{ fontSize: 13, color: '#8a97ac' }}>{data.model}</span>
        </div>

        <div>
          <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.1em', color: '#4c5a70', textTransform: 'uppercase', marginBottom: 10 }}>
            Good-match threshold
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <input
              type="range" min={0} max={1} step={0.05}
              value={data.good_similarity}
              onChange={e => update('good_similarity', Number(e.target.value))}
              style={{ flex: 1 }}
            />
            <span className="mono" style={{ fontSize: 13, color: '#cdd6e3', width: 40 }}>{Math.round(data.good_similarity * 100)}%</span>
          </div>
          <p style={{ color: '#8a97ac', fontSize: 12, marginTop: 6 }}>
            Evidence similarity at or above this counts a requirement "complete" (Gaps Report).
          </p>
        </div>

        <div>
          <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.1em', color: '#4c5a70', textTransform: 'uppercase', marginBottom: 10 }}>
            Partial-match threshold
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <input
              type="range" min={0} max={1} step={0.05}
              value={data.partial_similarity}
              onChange={e => update('partial_similarity', Number(e.target.value))}
              style={{ flex: 1 }}
            />
            <span className="mono" style={{ fontSize: 13, color: '#cdd6e3', width: 40 }}>{Math.round(data.partial_similarity * 100)}%</span>
          </div>
          <p style={{ color: '#8a97ac', fontSize: 12, marginTop: 6 }}>
            Below "good" but at or above this counts "linked" rather than a hard gap.
          </p>
        </div>

        <div>
          <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.1em', color: '#4c5a70', textTransform: 'uppercase', marginBottom: 10 }}>
            Evidence chunks per requirement (top-K)
          </div>
          <input
            className="input-field"
            type="number" min={1} max={20}
            value={data.top_k}
            onChange={e => update('top_k', Number(e.target.value))}
            style={{ width: 100 }}
          />
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
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
