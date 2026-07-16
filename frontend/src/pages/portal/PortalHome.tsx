import { useEffect, useState } from 'react';
import { Link } from '../../router';
import { getPipeline } from '../../api';
import type { PipelineEntry } from '../../types';
import { daysLeft, displayTagLine } from '../../utils';

export default function PortalHome() {
  const [pipeline, setPipeline] = useState<PipelineEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    getPipeline()
      .then(setPipeline)
      .catch(() => setError('Failed to load pipeline data'))
      .finally(() => setLoading(false));
  }, []);

  const alerts = pipeline.filter(e => {
    const dl = e.deadline_override || e.deadline;
    const d = daysLeft(dl);
    return d !== null && d <= 14 && e.submission_status !== 'submitted';
  }).sort((a, b) => {
    const da = daysLeft(a.deadline_override || a.deadline) ?? 999;
    const db = daysLeft(b.deadline_override || b.deadline) ?? 999;
    return da - db;
  });

  return (
    <div>
      <h1 style={{ fontSize: 28, fontWeight: 700, marginBottom: 4 }}>Welcome to Tenderizer</h1>
      <p style={{ color: '#8892a4', marginBottom: 28 }}>Your tender pipeline at a glance — and a launchpad for every tool.</p>

      {/* App launch cards */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, marginBottom: 32, maxWidth: 720 }}>
        {/* Scout */}
        <Link to="/scout/dashboard" className="card" style={{ padding: 20, display: 'block', transition: 'border-color 0.15s', borderColor: '#1a2334' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
            <div style={{ width: 36, height: 36, background: 'rgba(46,230,212,0.1)', borderRadius: 8, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#2EE6D4', fontSize: 18 }}>◎</div>
            <span style={{ background: 'rgba(52,211,153,0.15)', color: '#34d399', border: '1px solid rgba(52,211,153,0.3)', borderRadius: 9999, fontSize: 11, fontWeight: 700, padding: '2px 8px' }}>LIVE</span>
          </div>
          <div style={{ fontWeight: 700, fontSize: 17, marginBottom: 4 }}>Scout</div>
          <div style={{ color: '#8892a4', fontSize: 13, marginBottom: 14 }}>Monitor & match tenders</div>
          <div style={{ color: '#2EE6D4', fontSize: 13, fontWeight: 500 }}>Open Scout →</div>
        </Link>

        {/* Vault */}
        <Link to="/vault" className="card" style={{ padding: 20, display: 'block' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
            <div style={{ width: 36, height: 36, background: 'rgba(96,165,250,0.1)', borderRadius: 8, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#60a5fa', fontSize: 18 }}>▦</div>
            <span style={{ background: 'rgba(227,179,65,0.15)', color: '#e3b341', border: '1px solid rgba(227,179,65,0.3)', borderRadius: 9999, fontSize: 11, fontWeight: 700, padding: '2px 8px' }}>DEV</span>
          </div>
          <div style={{ fontWeight: 700, fontSize: 17, marginBottom: 4 }}>Vault</div>
          <div style={{ color: '#8892a4', fontSize: 13, marginBottom: 14 }}>Technical document library</div>
          <div style={{ color: '#60a5fa', fontSize: 13, fontWeight: 500 }}>Open Vault →</div>
        </Link>

        {/* Composer */}
        <Link to="/composer" className="card" style={{ padding: 20, display: 'block' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
            <div style={{ width: 36, height: 36, background: 'rgba(192,132,252,0.1)', borderRadius: 8, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#c084fc', fontSize: 18 }}>✎</div>
            <span style={{ background: 'rgba(227,179,65,0.15)', color: '#e3b341', border: '1px solid rgba(227,179,65,0.3)', borderRadius: 9999, fontSize: 11, fontWeight: 700, padding: '2px 8px' }}>DEV</span>
          </div>
          <div style={{ fontWeight: 700, fontSize: 17, marginBottom: 4 }}>Composer</div>
          <div style={{ color: '#8892a4', fontSize: 13, marginBottom: 14 }}>Draft tender proposals</div>
          <div style={{ color: '#c084fc', fontSize: 13, fontWeight: 500 }}>Open Composer →</div>
        </Link>
      </div>

      {/* Deadline alerts */}
      {alerts.length > 0 && (
        <div style={{ marginBottom: 28, maxWidth: 720 }}>
          <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.08em', color: '#4c5a70', textTransform: 'uppercase', marginBottom: 10 }}>Deadline Alerts</div>
          {alerts.map(e => {
            const dl = e.deadline_override || e.deadline;
            const d = daysLeft(dl) ?? 999;
            const isUrgent = d <= 7;
            return (
              <div
                key={e.pub_number}
                style={{
                  background: isUrgent ? 'rgba(248,113,113,0.08)' : 'rgba(227,179,65,0.08)',
                  border: `1px solid ${isUrgent ? 'rgba(248,113,113,0.3)' : 'rgba(227,179,65,0.3)'}`,
                  borderRadius: 8, padding: '12px 16px', marginBottom: 8,
                  display: 'flex', alignItems: 'center', gap: 12,
                }}
              >
                <span style={{ fontSize: 16 }}>{isUrgent ? '🔴' : '🟡'}</span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontWeight: 500, fontSize: 13, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {displayTagLine(e)}
                  </div>
                  <div style={{ fontSize: 12, color: isUrgent ? '#f87171' : '#e3b341', marginTop: 2 }}>
                    {isUrgent
                      ? `Closes in ${d} day${d !== 1 ? 's' : ''} and no tender has been sent — act now or request an extension.`
                      : `Deadline in ${d} day${d !== 1 ? 's' : ''} — submission in progress`}
                  </div>
                </div>
                <Link to="/portal/pipeline" style={{ color: '#2EE6D4', fontSize: 13, fontWeight: 500, whiteSpace: 'nowrap' }}>View →</Link>
              </div>
            );
          })}
        </div>
      )}

      {/* CR-002 F: the tabular accepted-tenders snapshot that used to live here
          moved to the new Calendar screen (now the Portal's default landing) —
          see PortalCalendar.tsx. Home stays the app launchpad + deadline alerts. */}
      {!loading && !error && (
        <div style={{ maxWidth: 720 }}>
          <Link to="/portal/calendar" className="card" style={{ padding: '14px 16px', display: 'flex', alignItems: 'center', gap: 10, textDecoration: 'none' }}>
            <span style={{ color: '#e2e8f0', fontSize: 14, fontWeight: 500 }}>View accepted tenders on the Calendar</span>
            <span style={{ marginLeft: 'auto', color: '#2EE6D4', fontSize: 13 }}>Open Calendar →</span>
          </Link>
        </div>
      )}
    </div>
  );
}
