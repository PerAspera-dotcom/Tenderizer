import { useEffect, useState } from 'react';
import { getFollowup, patchFollowup } from '../../api';
import type { FollowupEntry } from '../../types';
import { formatDate, countryFlag } from '../../utils';

export default function PortalFollowup() {
  const [entries, setEntries] = useState<FollowupEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  function load() {
    getFollowup()
      .then(setEntries)
      .catch(() => setError('Failed to load follow-up data'))
      .finally(() => setLoading(false));
  }

  useEffect(() => { load(); }, []);

  async function setOutcome(pub_number: string, outcome: string) {
    await patchFollowup(pub_number, outcome);
    load();
  }

  const won = entries.filter(e => e.outcome === 'won').length;
  const lost = entries.filter(e => e.outcome === 'lost').length;
  const awaiting = entries.filter(e => e.outcome === 'pending').length;
  const closed = won + lost;
  const winRate = closed > 0 ? Math.round((won / closed) * 100) : 0;

  return (
    <div>
      <h1 style={{ fontSize: 28, fontWeight: 700, marginBottom: 4 }}>Follow-up & Results</h1>
      <p style={{ color: '#8892a4', marginBottom: 16 }}>Tenders you've submitted — chase results and record won / lost outcomes</p>

      <div style={{ display: 'flex', gap: 10, marginBottom: 20, flexWrap: 'wrap' }}>
        {won > 0 && <span className="pill pill-green">✓ {won} won</span>}
        {lost > 0 && <span className="pill pill-red">✗ {lost} lost</span>}
        {awaiting > 0 && <span className="pill pill-amber">⏳ {awaiting} awaiting</span>}
        {closed > 0 && (
          <span className="pill pill-green" style={{ fontWeight: 700 }}>{winRate}% win rate</span>
        )}
      </div>

      {loading ? (
        <div className="loading">Loading…</div>
      ) : error ? (
        <div className="error">{error}</div>
      ) : entries.length === 0 ? (
        <div className="card" style={{ padding: 32, textAlign: 'center', color: '#8892a4' }}>
          No submitted tenders yet. Change submission status to "Tender sent" in the Pipeline view.
        </div>
      ) : (
        <div className="card">
          <table>
            <thead>
              <tr>
                <th>Tender</th>
                <th>Portal</th>
                <th>Submitted</th>
                <th>Result Due</th>
                <th>Outcome</th>
              </tr>
            </thead>
            <tbody>
              {entries.map(e => (
                <tr key={e.pub_number}>
                  <td>
                    <div style={{ fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 280 }}>
                      {e.tag_line}
                    </div>
                    <div style={{ color: '#8892a4', fontSize: 12, marginTop: 2 }}>
                      {countryFlag(e.country)} {e.buyer}
                    </div>
                  </td>
                  <td>
                    <span style={{ background: '#1a2334', color: '#e2e8f0', padding: '3px 8px', borderRadius: 4, fontSize: 12, fontWeight: 600 }}>
                      {e.source}
                    </span>
                  </td>
                  <td>
                    <span className="mono" style={{ fontSize: 13 }}>{formatDate(e.submitted_date)}</span>
                  </td>
                  <td>
                    <span className="mono" style={{ fontSize: 13 }}>{formatDate(e.result_due)}</span>
                  </td>
                  <td>
                    {e.outcome === 'pending' && (
                      <div style={{ display: 'flex', gap: 6 }}>
                        <button className="btn btn-green" style={{ fontSize: 12, padding: '5px 10px' }} onClick={() => setOutcome(e.pub_number, 'won')}>
                          Mark won
                        </button>
                        <button className="btn btn-red" style={{ fontSize: 12, padding: '5px 10px' }} onClick={() => setOutcome(e.pub_number, 'lost')}>
                          Mark lost
                        </button>
                      </div>
                    )}
                    {e.outcome === 'won' && (
                      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                        <span style={{ background: 'rgba(52,211,153,0.2)', color: '#34d399', border: '1px solid rgba(52,211,153,0.4)', padding: '4px 12px', borderRadius: 6, fontSize: 13, fontWeight: 600 }}>
                          ✓ Won
                        </span>
                        <button onClick={() => setOutcome(e.pub_number, 'pending')} style={{ background: 'none', border: 'none', color: '#8892a4', fontSize: 12, textDecoration: 'underline', cursor: 'pointer' }}>
                          reopen
                        </button>
                      </div>
                    )}
                    {e.outcome === 'lost' && (
                      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                        <span style={{ background: 'rgba(248,113,113,0.2)', color: '#f87171', border: '1px solid rgba(248,113,113,0.4)', padding: '4px 12px', borderRadius: 6, fontSize: 13, fontWeight: 600 }}>
                          ✗ Lost
                        </span>
                        <button onClick={() => setOutcome(e.pub_number, 'pending')} style={{ background: 'none', border: 'none', color: '#8892a4', fontSize: 12, textDecoration: 'underline', cursor: 'pointer' }}>
                          reopen
                        </button>
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
