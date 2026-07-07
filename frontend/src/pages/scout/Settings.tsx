import { useEffect, useState } from 'react';
import { getSettingsConfig, putSettingsConfig } from '../../api';
import type { SettingsConfig } from '../../types';

export default function Settings() {
  const [data, setData] = useState<SettingsConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    getSettingsConfig()
      .then(setData)
      .catch(() => setError('Failed to load settings'))
      .finally(() => setLoading(false));
  }, []);

  function update<K extends keyof SettingsConfig>(key: K, value: SettingsConfig[K]) {
    setData(d => d && { ...d, [key]: value });
    setSaved(false);
  }

  async function handleSave() {
    if (!data) return;
    setSaving(true);
    setError('');
    try {
      await putSettingsConfig(data);
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
      <h1 style={{ fontSize: 28, fontWeight: 700, marginBottom: 4 }}>Settings</h1>
      <p style={{ color: '#8892a4', marginBottom: 20 }}>
        Schedule, run window and notification preferences
      </p>

      <div style={{
        background: 'rgba(227,179,65,0.07)', border: '1px solid rgba(227,179,65,0.3)',
        borderRadius: 8, padding: '10px 14px', fontSize: 12, color: '#e3b341', marginBottom: 20, maxWidth: 520,
      }}>
        These are saved preferences only — there's no scheduler or email delivery wired up yet, so
        runs still happen via "Run now" and no notification will actually be sent.
      </div>

      <div className="card" style={{ padding: '20px 24px', maxWidth: 520, display: 'flex', flexDirection: 'column', gap: 20 }}>
        <div>
          <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.1em', color: '#4c5a70', textTransform: 'uppercase', marginBottom: 10 }}>
            Schedule
          </div>
          <select
            value={data.run_frequency}
            onChange={e => update('run_frequency', e.target.value)}
            style={{ background: '#0f1623', border: '1px solid #1a2334', color: '#e2e8f0', padding: '7px 12px', borderRadius: 6, fontSize: 13, cursor: 'pointer', outline: 'none' }}
          >
            <option value="daily">Daily</option>
            <option value="weekly">Weekly</option>
            <option value="paused">Paused</option>
          </select>
        </div>

        <div>
          <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.1em', color: '#4c5a70', textTransform: 'uppercase', marginBottom: 10 }}>
            Run window
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <input
              className="input-field"
              type="time"
              value={data.run_window_start}
              onChange={e => update('run_window_start', e.target.value)}
              style={{ width: 130 }}
            />
            <span style={{ color: '#8892a4', fontSize: 13 }}>to</span>
            <input
              className="input-field"
              type="time"
              value={data.run_window_end}
              onChange={e => update('run_window_end', e.target.value)}
              style={{ width: 130 }}
            />
          </div>
        </div>

        <div>
          <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.1em', color: '#4c5a70', textTransform: 'uppercase', marginBottom: 10 }}>
            Notifications
          </div>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, marginBottom: 10, cursor: 'pointer' }}>
            <input
              type="checkbox"
              checked={data.notify_on_complete}
              onChange={e => update('notify_on_complete', e.target.checked)}
            />
            Notify me when a run completes
          </label>
          <input
            className="input-field"
            type="email"
            placeholder="you@example.com"
            value={data.notify_email}
            onChange={e => update('notify_email', e.target.value)}
            disabled={!data.notify_on_complete}
            style={{ maxWidth: 280 }}
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
