import { useEffect, useState } from 'react';
import { getScoutSettings, putScoutSettings } from '../../api';
import type { ScoutSettings } from '../../types';

// CR-007 Phase D (D2): formerly a hard-coded 72h hard exclude (filters.py's
// removed check_deadline_too_soon) — now a tenant-editable, purely advisory
// window. A tender past it still shows up everywhere, just flagged
// "closing soon" (see ReviewQueue.tsx/TenderFeed.tsx).
export default function DeadlineConfig() {
  const [data, setData] = useState<ScoutSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    getScoutSettings()
      .then(setData)
      .catch(() => setError('Failed to load deadline settings'))
      .finally(() => setLoading(false));
  }, []);

  async function handleSave() {
    if (!data) return;
    setSaving(true);
    setError('');
    try {
      await putScoutSettings({ deadline_floor_hours: data.deadline_floor_hours });
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
          "Closing soon" window
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <input
            type="range"
            min={6} max={168} step={6}
            value={data.deadline_floor_hours}
            onChange={e => { setData({ ...data, deadline_floor_hours: Number(e.target.value) }); setSaved(false); }}
            style={{ flex: 1 }}
          />
          <span className="mono" style={{ fontSize: 13, color: '#cdd6e3', width: 70, textAlign: 'right' }}>
            {data.deadline_floor_hours}h
          </span>
        </div>
        <p style={{ color: '#8a97ac', fontSize: 12, marginTop: 8 }}>
          A tender due within this window is flagged "closing soon" in the Review Queue and Tender
          Feed — it's never hidden, just highlighted, since how much lead time a bid actually needs
          is business-dependent. Default is 72h (3 days).
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
