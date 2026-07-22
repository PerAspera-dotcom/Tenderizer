import { useEffect, useState } from 'react';
import { getVaultSettings, putVaultSettings } from '../../api';
import type { VaultSettings as VaultSettingsType } from '../../types';

export default function VaultSettings() {
  const [data, setData] = useState<VaultSettingsType | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    getVaultSettings()
      .then(setData)
      .catch(() => setError('Failed to load Vault settings'))
      .finally(() => setLoading(false));
  }, []);

  async function handleSave() {
    if (!data) return;
    setSaving(true);
    setError('');
    try {
      await putVaultSettings({ confidence_threshold: data.confidence_threshold });
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
        <h1 style={{ fontSize: 28, fontWeight: 700 }}>Vault Settings</h1>
        <span style={{ background: 'rgba(96,165,250,0.15)', color: '#60a5fa', border: '1px solid rgba(96,165,250,0.3)', borderRadius: 6, fontSize: 11, fontWeight: 700, padding: '3px 8px', letterSpacing: '0.06em' }}>
          VAULT
        </span>
      </div>
      <p style={{ color: '#8a97ac', marginBottom: 20, fontSize: 13 }}>
        Extraction model and confidence threshold for new document ingests
      </p>

      <div className="card" style={{ padding: '20px 24px', maxWidth: 480, display: 'flex', flexDirection: 'column', gap: 20 }}>
        <div>
          <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.1em', color: '#4c5a70', textTransform: 'uppercase', marginBottom: 10 }}>
            Extraction model
          </div>
          <span className="mono" style={{ fontSize: 13, color: '#8a97ac' }}>{data.extraction_model}</span>
        </div>

        <div>
          <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.1em', color: '#4c5a70', textTransform: 'uppercase', marginBottom: 10 }}>
            Confidence threshold
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <input
              type="range"
              min={0} max={1} step={0.05}
              value={data.confidence_threshold}
              onChange={e => { setData({ ...data, confidence_threshold: Number(e.target.value) }); setSaved(false); }}
              style={{ flex: 1 }}
            />
            <span className="mono" style={{ fontSize: 13, color: '#cdd6e3', width: 40 }}>
              {Math.round(data.confidence_threshold * 100)}%
            </span>
          </div>
          <p style={{ color: '#8a97ac', fontSize: 12, marginTop: 8 }}>
            Documents extracted with confidence below this are flagged "needs review" in the Library
            instead of "indexed", so an analyst notices low-confidence extractions.
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
    </div>
  );
}
