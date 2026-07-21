import { useEffect, useState } from 'react';
import { Link } from '../../router';
import { getStats, getHealth, listTenders, getLatestReportBlob, ReportNotFoundError } from '../../api';
import type { Stats, PortalHealth, Tender } from '../../types';
import { formatDate, formatTime, countryFlag, displayTagLine } from '../../utils';
import MatchChip from '../../components/MatchChip';

export default function Dashboard() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [health, setHealth] = useState<PortalHealth[]>([]);
  const [topTenders, setTopTenders] = useState<Tender[]>([]);
  const [loading, setLoading] = useState(true);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState('');

  function load() {
    Promise.all([
      getStats(),
      getHealth(),
      listTenders({ limit: 5, sort: 'deadline' }),
    ]).then(([s, h, t]) => {
      setStats(s);
      setHealth(h);
      setTopTenders(t.results.filter(r => r.status !== 'dismissed'));
    }).catch(() => {}).finally(() => setLoading(false));
  }

  useEffect(() => { load(); }, []);

  // CR-004/5 UX pass: Reports used to be its own nav item + page whose only
  // content beyond this same last-sync info was this one export button —
  // folded in here instead of a whole separate screen for one action.
  async function handleExport() {
    setExporting(true);
    setExportError('');
    try {
      const blob = await getLatestReportBlob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'tenders.xlsx';
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      setExportError(e instanceof ReportNotFoundError ? 'No report yet — run the pipeline first.' : 'Export failed — try again.');
    } finally {
      setExporting(false);
    }
  }

  function formatLastSync(ts: string | null): string {
    if (!ts) return 'Never synced';
    const d = new Date(ts);
    const today = new Date();
    const isToday = d.toDateString() === today.toDateString();
    return isToday ? `today at ${formatTime(ts)}` : formatDate(ts);
  }

  function formatNextRun(nr: string | null): string {
    if (!nr) return '—';
    const ms = new Date(nr).getTime() - Date.now();
    if (ms < 0) return 'soon';
    const h = Math.floor(ms / 3600000);
    const m = Math.floor((ms % 3600000) / 60000);
    return h > 0 ? `${h}h ${m}m` : `${m}m`;
  }

  const cpvMatched = (stats?.by_match.cpv ?? 0) + (stats?.by_match.both ?? 0);
  const kwOnly = stats?.by_match.keyword ?? 0;
  // All-time matched pool (by_match is a live, cumulative count) — distinct
  // from stats.matched_total, which is scoped to just the last sync (see the
  // "Last sync" strip below) and would understate the Tender Feed's actual pool.
  const allTimeMatched = cpvMatched + kwOnly;

  function healthDot(s: string) {
    if (s === 'live') return <span className="dot-live" />;
    if (s === 'paused') return <span className="dot-paused" />;
    return <span className="dot-planned" />;
  }

  return (
    <div>
      <h1 style={{ fontSize: 28, fontWeight: 700, marginBottom: 4 }}>Dashboard</h1>
      <p style={{ color: '#8892a4', marginBottom: 20 }}>Daily digest for the tent & shelter supply sector</p>

      {loading ? <div className="loading">Loading…</div> : (
        <>
          {/* Last-run strip */}
          <div className="card" style={{ padding: '14px 20px', marginBottom: 20, display: 'flex', alignItems: 'center', gap: 0, flexWrap: 'wrap' }}>
            <span className="dot-live" style={{ marginRight: 8 }} />
            <span style={{ fontSize: 14 }}>Last sync <strong>{formatLastSync(stats?.last_sync ?? null)}</strong></span>
            <span style={{ color: '#1a2334', margin: '0 16px', fontSize: 18 }}>|</span>
            <span style={{ color: '#8892a4', fontSize: 14 }}><strong style={{ color: '#e2e8f0' }}>{stats?.notices_scanned ?? 0}</strong> notices scanned</span>
            <span style={{ color: '#1a2334', margin: '0 16px', fontSize: 18 }}>|</span>
            <span style={{ color: '#8892a4', fontSize: 14 }}><strong style={{ color: '#e2e8f0' }}>{stats?.matched_total ?? 0}</strong> matched</span>
            {/* CR-002 B2: distinct count/link, kept out of the active KPI cards */}
            {(stats?.past_tenders ?? 0) > 0 && (
              <>
                <span style={{ color: '#1a2334', margin: '0 16px', fontSize: 18 }}>|</span>
                <Link to="/scout/past-tenders" style={{ color: '#8892a4', fontSize: 14, textDecoration: 'none' }}>
                  <strong style={{ color: '#e2e8f0' }}>{stats?.past_tenders}</strong> past tenders
                </Link>
              </>
            )}
            <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 14 }}>
              {stats?.next_run && (
                <span style={{ color: '#8892a4', fontSize: 13 }}>Next run in <strong style={{ color: '#e2e8f0' }}>{formatNextRun(stats.next_run)}</strong></span>
              )}
              {/* Reports (its own nav item/page before this pass) folded in
                  here — same last-sync context, one fewer screen to learn. */}
              <button className="btn btn-ghost" onClick={handleExport} disabled={exporting} style={{ fontSize: 13 }}>
                {exporting ? 'Preparing…' : '⤓ Export to Excel'}
              </button>
              {/* "Run now" itself lives in the top bar now (Scout-scoped) —
                  this panel had its own separate copy of the exact same
                  action, which was pure duplication. */}
            </div>
          </div>
          {exportError && <div style={{ color: '#f87171', fontSize: 13, marginTop: -12, marginBottom: 20 }}>{exportError}</div>}

          {/* KPI cards */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 14, marginBottom: 20 }}>
            <div className="card" style={{ padding: '18px 20px' }}>
              <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.08em', color: '#4c5a70', textTransform: 'uppercase', marginBottom: 12 }}>New Tenders Today</div>
              <div style={{ fontSize: 36, fontWeight: 800, color: '#e2e8f0', lineHeight: 1 }}>{stats?.new_today ?? 0}</div>
              <div style={{ color: '#34d399', fontSize: 12, marginTop: 8 }}>▲ in the last 24h</div>
            </div>
            <div className="card" style={{ padding: '18px 20px' }}>
              <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.08em', color: '#4c5a70', textTransform: 'uppercase', marginBottom: 12 }}>Portals Active</div>
              <div style={{ fontSize: 36, fontWeight: 800, color: '#e2e8f0', lineHeight: 1 }}>
                {(stats?.portals_active ?? '2/4').split('/')[0]}
                <span style={{ fontSize: 20, fontWeight: 400, color: '#8892a4' }}> / {(stats?.portals_active ?? '2/4').split('/')[1]}</span>
              </div>
              <div style={{ color: '#e3b341', fontSize: 12, marginTop: 8 }}>⚠ BE planned · DE paused</div>
            </div>
            <div className="card" style={{ padding: '18px 20px' }}>
              <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.08em', color: '#4c5a70', textTransform: 'uppercase', marginBottom: 12 }}>Matched by CPV</div>
              <div style={{ fontSize: 36, fontWeight: 800, color: '#2EE6D4', lineHeight: 1 }}>{cpvMatched}</div>
              <div style={{ color: '#8892a4', fontSize: 12, marginTop: 8 }}>code-based matches</div>
            </div>
            <div className="card" style={{ padding: '18px 20px' }}>
              <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.08em', color: '#4c5a70', textTransform: 'uppercase', marginBottom: 12 }}>Keyword Only</div>
              <div style={{ fontSize: 36, fontWeight: 800, color: '#60a5fa', lineHeight: 1 }}>{kwOnly}</div>
              <div style={{ color: '#8892a4', fontSize: 12, marginTop: 8 }}>no CPV match</div>
            </div>
          </div>

          {/* Bottom panels */}
          <div style={{ display: 'grid', gridTemplateColumns: '3fr 2fr', gap: 16 }}>
            {/* Tender Feed panel */}
            <div className="card">
              <div style={{ padding: '14px 16px', borderBottom: '1px solid #1a2334', display: 'flex', alignItems: 'center', gap: 10 }}>
                <span style={{ fontWeight: 600, fontSize: 15 }}>Tender Feed</span>
                <span style={{ background: 'rgba(52,211,153,0.15)', color: '#34d399', border: '1px solid rgba(52,211,153,0.3)', fontSize: 11, fontWeight: 700, padding: '2px 8px', borderRadius: 9999 }}>✓ LIVE</span>
                <span style={{ marginLeft: 'auto', color: '#8892a4', fontSize: 12 }}>Showing {topTenders.length} of {allTimeMatched} matched</span>
              </div>
              <table>
                <thead>
                  <tr>
                    <th>Title</th>
                    <th>Portal</th>
                    <th>Deadline</th>
                    <th>Match</th>
                    <th>Open</th>
                  </tr>
                </thead>
                <tbody>
                  {topTenders.map(t => (
                    <tr key={t.hash}>
                      <td>
                        <div style={{ fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 240, fontSize: 13 }}>{displayTagLine(t)}</div>
                        <div style={{ color: '#8892a4', fontSize: 11, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 240 }}>{t.buyer}</div>
                      </td>
                      <td style={{ whiteSpace: 'nowrap' }}>
                        <span style={{ fontSize: 14 }}>{countryFlag(t.country)}</span>{' '}
                        <span style={{ background: '#1a2334', color: '#e2e8f0', padding: '2px 6px', borderRadius: 4, fontSize: 11, fontWeight: 600 }}>{t.source}</span>
                      </td>
                      <td><span className="mono" style={{ fontSize: 12 }}>{formatDate(t.deadline)}</span></td>
                      <td><MatchChip matchSource={t.match_source} /></td>
                      <td>
                        {t.url ? (
                          <a href={t.url} target="_blank" rel="noopener noreferrer" className="btn btn-ghost" style={{ fontSize: 12, padding: '4px 10px' }}>Open ↗</a>
                        ) : '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div style={{ padding: '10px 16px', borderTop: '1px solid #1a2334' }}>
                <Link to="/scout/tender-feed" style={{ color: '#2EE6D4', fontSize: 13 }}>View all →</Link>
              </div>
            </div>

            {/* Portal Health panel */}
            <div className="card">
              <div style={{ padding: '14px 16px', borderBottom: '1px solid #1a2334', fontWeight: 600, fontSize: 15 }}>Portal Health</div>
              <div style={{ padding: '4px 0' }}>
                {health.map(p => (
                  <div key={p.name} style={{ padding: '12px 16px', borderBottom: '1px solid #1a2334', display: 'flex', alignItems: 'center', gap: 10 }}>
                    {healthDot(p.status)}
                    <div style={{ flex: 1 }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        <span style={{ fontWeight: 500, fontSize: 13 }}>{p.name}</span>
                        <span style={{ color: '#8892a4', fontSize: 12 }}>{p.region}</span>
                        {p.status === 'live' && (
                          <span style={{ color: '#2EE6D4', fontSize: 11, fontWeight: 600, marginLeft: 'auto' }}>live</span>
                        )}
                        {p.status === 'paused' && (
                          <span style={{ color: '#e3b341', fontSize: 11, fontWeight: 600, marginLeft: 'auto' }}>Paused</span>
                        )}
                        {p.status === 'planned' && (
                          <span style={{ color: '#4c5a70', fontSize: 11, fontWeight: 600, marginLeft: 'auto' }}>Planned</span>
                        )}
                      </div>
                      {p.last_result && <div style={{ color: '#8892a4', fontSize: 11, marginTop: 2 }}>{p.last_result}</div>}
                      {p.detail && <div style={{ color: '#e3b341', fontSize: 11, marginTop: 2 }}>{p.detail}</div>}
                      {(p.streak_ok_days > 0 || p.consecutive_failures > 0) && (
                        <div
                          style={{
                            color: p.consecutive_failures > 0 ? '#f87171' : '#2EE6D4',
                            fontSize: 11, marginTop: 2, fontWeight: 600,
                          }}
                          title={p.last_failure ? `Last failure: ${p.last_failure}` : undefined}
                        >
                          {p.consecutive_failures > 0
                            ? `✗ ${p.consecutive_failures} failed in a row`
                            : `✓ ${p.streak_ok_days} day${p.streak_ok_days === 1 ? '' : 's'}`}
                          {p.failures_7d > 0 && p.consecutive_failures === 0 && (
                            <span style={{ color: '#8892a4', fontWeight: 400 }}> · {p.failures_7d} failed in last 7d</span>
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
