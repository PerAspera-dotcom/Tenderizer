import { useEffect, useState } from 'react';
import { getHealth } from '../../api';
import type { PortalHealth } from '../../types';

const STATUS_LABEL: Record<string, { label: string; color: string }> = {
  live: { label: 'Live', color: '#2EE6D4' },
  paused: { label: 'Paused', color: '#e3b341' },
  planned: { label: 'Planned', color: '#4c5a70' },
};

// CR-004/5 UX pass: this nav item used to be a 🚧 stub even though the real
// per-source streak/failure data (CR-004 F4's source_health) already existed
// and was surfaced in miniature on the Dashboard — this is that same
// GET /api/health data, given the full page its nav label already promised.
export default function PortalsHealth() {
  const [health, setHealth] = useState<PortalHealth[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getHealth().then(setHealth).catch(() => {}).finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="loading">Loading…</div>;

  return (
    <div>
      <h1 style={{ fontSize: 28, fontWeight: 700, marginBottom: 4 }}>Portals & Health</h1>
      <p style={{ color: '#8892a4', marginBottom: 20 }}>
        Per-source scrape throughput and reliability — an ops alert fires automatically after 3
        consecutive failed days for any source; nothing is ever paused automatically.
      </p>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 14 }}>
        {health.map(p => {
          const meta = STATUS_LABEL[p.status] ?? { label: p.status, color: '#8892a4' };
          return (
            <div key={p.name} className="card" style={{ padding: '18px 20px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
                <span style={{ fontWeight: 700, fontSize: 16 }}>{p.name}</span>
                <span style={{ color: '#8892a4', fontSize: 12 }}>{p.region}</span>
                <span style={{
                  marginLeft: 'auto', color: meta.color, fontSize: 11, fontWeight: 700,
                  border: `1px solid ${meta.color}55`, background: `${meta.color}1a`,
                  borderRadius: 9999, padding: '2px 10px',
                }}>
                  {meta.label}
                </span>
              </div>

              {p.detail && (
                <div style={{ color: '#e3b341', fontSize: 12, marginBottom: 10 }}>{p.detail}</div>
              )}

              {p.last_result ? (
                <>
                  <div style={{ color: '#8892a4', fontSize: 12, marginBottom: 10 }}>
                    Last result: <span style={{ color: '#c8d0de' }}>{p.last_result}</span>
                  </div>
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 10 }}>
                    <div>
                      <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.08em', color: '#4c5a70', textTransform: 'uppercase', marginBottom: 4 }}>Streak</div>
                      <div className="mono" style={{ fontSize: 18, fontWeight: 700, color: p.consecutive_failures > 0 ? '#f87171' : '#2EE6D4' }}>
                        {p.consecutive_failures > 0 ? `✗ ${p.consecutive_failures}` : `✓ ${p.streak_ok_days}`}
                      </div>
                      <div style={{ fontSize: 11, color: '#8892a4' }}>
                        {p.consecutive_failures > 0 ? 'failed in a row' : `day${p.streak_ok_days === 1 ? '' : 's'} ok`}
                      </div>
                    </div>
                    <div>
                      <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.08em', color: '#4c5a70', textTransform: 'uppercase', marginBottom: 4 }}>Last 7 days</div>
                      <div className="mono" style={{ fontSize: 18, fontWeight: 700, color: p.failures_7d > 0 ? '#e3b341' : '#34d399' }}>
                        {p.failures_7d}
                      </div>
                      <div style={{ fontSize: 11, color: '#8892a4' }}>failed runs</div>
                    </div>
                    <div>
                      <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.08em', color: '#4c5a70', textTransform: 'uppercase', marginBottom: 4 }}>Last failure</div>
                      <div className="mono" style={{ fontSize: 13, color: '#c8d0de', marginTop: 4 }}>{p.last_failure ?? '—'}</div>
                    </div>
                  </div>
                </>
              ) : (
                <div style={{ color: '#4c5a70', fontSize: 12 }}>No scrape history yet.</div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
