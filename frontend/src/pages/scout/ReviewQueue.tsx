import { useEffect, useState } from 'react';
import { listTenders, patchTender } from '../../api';
import type { Tender } from '../../types';
import { formatDate, countryFlag, confidenceFromMatchSource } from '../../utils';
import MatchChip from '../../components/MatchChip';

function statusDotColor(status: string): string {
  if (status === 'shortlisted') return '#34d399';
  if (status === 'reviewed') return '#e3b341';
  if (status === 'dismissed') return '#f87171';
  return '#4c5a70';
}

function StatusBadge({ status }: { status: string }) {
  const label =
    status === 'shortlisted' ? '● Shortlisted' :
    status === 'reviewed' ? '● Reviewed' :
    status === 'dismissed' ? '● Dismissed' : '○ New — awaiting decision';
  const color =
    status === 'shortlisted' ? '#34d399' :
    status === 'reviewed' ? '#e3b341' :
    status === 'dismissed' ? '#f87171' : '#8892a4';
  return (
    <span style={{ background: `rgba(0,0,0,0.2)`, border: `1px solid ${color}`, color, borderRadius: 9999, padding: '3px 10px', fontSize: 12, fontWeight: 500 }}>
      {label}
    </span>
  );
}

function confidenceLabel(ms: string | null | undefined): string {
  if (!ms || ms === 'None' || ms === 'none') return 'Low confidence — no direct match';
  if (ms === 'both') return 'High confidence — matched by CPV + keywords';
  if (ms === 'cpv') return 'High confidence — matched by CPV code';
  if (ms === 'keyword') return 'Candidate — keyword match only';
  return ms;
}

export default function ReviewQueue() {
  const [tenders, setTenders] = useState<Tender[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [selected, setSelected] = useState<Tender | null>(null);
  const [patching, setPatching] = useState(false);

  function load() {
    listTenders({ limit: 500, sort: 'deadline' }).then(r => {
      const filtered = r.results.filter(t => t.status !== 'dismissed');
      // Sort: cpv/both first, then keyword, then none; within each by deadline
      const order = (t: Tender) => {
        if (t.match_source === 'both') return 0;
        if (t.match_source === 'cpv') return 1;
        if (t.match_source === 'keyword') return 2;
        return 3;
      };
      filtered.sort((a, b) => order(a) - order(b) || (a.deadline || '9999').localeCompare(b.deadline || '9999'));
      setTenders(filtered);
      setSelected(prev => {
        if (!prev) return filtered[0] ?? null;
        return filtered.find(t => t.pub_number === prev.pub_number) ?? filtered[0] ?? null;
      });
    }).catch(() => setError('Failed to load tenders'))
      .finally(() => setLoading(false));
  }

  useEffect(() => { load(); }, []);

  async function applyStatus(status: string) {
    if (!selected || patching) return;
    setPatching(true);
    try {
      await patchTender(selected.pub_number, status);
      load();
    } finally {
      setPatching(false);
    }
  }

  const newCount = tenders.filter(t => t.status === 'new').length;
  const shortlistedCount = tenders.filter(t => t.status === 'shortlisted').length;
  const reviewedCount = tenders.filter(t => t.status === 'reviewed').length;

  if (loading) return <div className="loading">Loading…</div>;
  if (error) return <div className="error">{error}</div>;

  return (
    <div>
      <h1 style={{ fontSize: 28, fontWeight: 700, marginBottom: 4 }}>Review Queue</h1>
      <p style={{ color: '#8892a4', marginBottom: 16 }}>Triage scored matches — confirm relevance before they reach the analyst's shortlist</p>

      <div style={{ display: 'flex', gap: 10, marginBottom: 20 }}>
        <span className="pill pill-grey">○ {newCount} new</span>
        <span className="pill pill-green">● {shortlistedCount} shortlisted</span>
        <span className="pill pill-amber">● {reviewedCount} reviewed</span>
      </div>

      {tenders.length === 0 ? (
        <div className="card" style={{ padding: 32, textAlign: 'center', color: '#8892a4' }}>No tenders to review.</div>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, alignItems: 'flex-start' }}>
          {/* Left list */}
          <div className="card" style={{ maxHeight: '75vh', overflowY: 'auto' }}>
            <div style={{ padding: '12px 16px', borderBottom: '1px solid #1a2334', fontSize: 11, fontWeight: 700, letterSpacing: '0.08em', color: '#8892a4', textTransform: 'uppercase', position: 'sticky', top: 0, background: '#151d2c', zIndex: 1 }}>
              Queue · {tenders.length} matches
            </div>
            {tenders.map(t => {
              const conf = confidenceFromMatchSource(t.match_source);
              const isActive = selected?.pub_number === t.pub_number;
              const dotColor = statusDotColor(t.status);
              const barColor = conf >= 80 ? '#2EE6D4' : '#e3b341';
              return (
                <div
                  key={t.pub_number}
                  onClick={() => setSelected(t)}
                  style={{
                    padding: '12px 14px', cursor: 'pointer', borderBottom: '1px solid #1a2334',
                    background: isActive ? 'rgba(46,230,212,0.05)' : 'transparent',
                    borderLeft: `3px solid ${isActive ? '#2EE6D4' : 'transparent'}`,
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8 }}>
                    <div style={{ width: 10, height: 10, borderRadius: '50%', border: `2px solid ${dotColor}`, background: t.status !== 'new' ? dotColor : 'transparent', marginTop: 3, flexShrink: 0 }} />
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontWeight: 500, fontSize: 13, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: isActive ? '#e2e8f0' : '#c8d0de' }}>
                        {t.tag_line}
                      </div>
                      <div style={{ color: '#8892a4', fontSize: 11, marginTop: 2 }}>
                        {t.source} · {t.country}
                      </div>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 6 }}>
                        <div style={{ flex: 1, height: 3, background: '#1a2334', borderRadius: 9999, overflow: 'hidden' }}>
                          <div style={{ width: `${conf}%`, height: '100%', background: barColor, borderRadius: 9999 }} />
                        </div>
                        <span style={{ color: '#8892a4', fontSize: 11, whiteSpace: 'nowrap' }}>{conf}%</span>
                      </div>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>

          {/* Right detail */}
          {selected && (
            <div className="card" style={{ maxHeight: '75vh', overflowY: 'auto' }}>
              <div style={{ padding: '16px 20px' }}>
                {/* Header */}
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12, flexWrap: 'wrap' }}>
                  <span style={{ background: '#1a2334', color: '#e2e8f0', padding: '2px 8px', borderRadius: 4, fontSize: 12, fontWeight: 600 }}>{selected.source}</span>
                  <span style={{ fontSize: 16 }}>{countryFlag(selected.country)}</span>
                  <span style={{ color: '#8892a4', fontSize: 13 }}>{selected.country}</span>
                  <div style={{ marginLeft: 'auto' }}>
                    <StatusBadge status={selected.status} />
                  </div>
                </div>
                <h2 style={{ fontSize: 20, fontWeight: 700, marginBottom: 20, lineHeight: 1.3 }}>{selected.tag_line}</h2>

                {/* Core elements */}
                <div style={{ marginBottom: 20 }}>
                  <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.08em', color: '#4c5a70', textTransform: 'uppercase', marginBottom: 12 }}>Core Elements</div>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px 20px' }}>
                    <div>
                      <div style={{ fontSize: 10, fontWeight: 700, color: '#4c5a70', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 3 }}>Issuing Authority</div>
                      <div style={{ fontSize: 13 }}>{selected.buyer || '—'}</div>
                    </div>
                    <div>
                      <div style={{ fontSize: 10, fontWeight: 700, color: '#4c5a70', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 3 }}>Country</div>
                      <div style={{ fontSize: 13 }}>{countryFlag(selected.country)} {selected.country}</div>
                    </div>
                    <div>
                      <div style={{ fontSize: 10, fontWeight: 700, color: '#4c5a70', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 3 }}>Region</div>
                      <div style={{ fontSize: 13 }}>{selected.place || '—'}</div>
                    </div>
                    <div>
                      <div style={{ fontSize: 10, fontWeight: 700, color: '#4c5a70', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 3 }}>Source Portal</div>
                      <div style={{ fontSize: 13 }}>{selected.source}</div>
                    </div>
                    <div>
                      <div style={{ fontSize: 10, fontWeight: 700, color: '#4c5a70', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 3 }}>Procedure Type</div>
                      <div style={{ fontSize: 13 }}>{selected.procedure || '—'}</div>
                    </div>
                    <div>
                      <div style={{ fontSize: 10, fontWeight: 700, color: '#4c5a70', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 3 }}>Deadline</div>
                      <div className="mono" style={{ fontSize: 13 }}>{formatDate(selected.deadline)}</div>
                    </div>
                  </div>

                  {selected.cpv_codes?.length > 0 && (
                    <div style={{ marginTop: 12 }}>
                      <div style={{ fontSize: 10, fontWeight: 700, color: '#4c5a70', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 6 }}>CPV Codes</div>
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                        {/* stable order, unique only — the engine dedupes at ingest, this is a defensive backstop */}
                        {Array.from(new Set(selected.cpv_codes)).map(c => (
                          <span key={c} className="mono" style={{ background: 'rgba(52,211,153,0.1)', color: '#34d399', border: '1px solid rgba(52,211,153,0.25)', padding: '2px 8px', borderRadius: 4, fontSize: 12 }}>{c}</span>
                        ))}
                      </div>
                    </div>
                  )}

                  {selected.matched_terms?.length > 0 && (
                    <div style={{ marginTop: 12 }}>
                      <div style={{ fontSize: 10, fontWeight: 700, color: '#4c5a70', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 6 }}>Matched Terms</div>
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                        {selected.matched_terms.map(term => (
                          <span key={term} style={{ background: 'rgba(96,165,250,0.1)', color: '#60a5fa', border: '1px solid rgba(96,165,250,0.25)', padding: '2px 8px', borderRadius: 4, fontSize: 12 }}>{term}</span>
                        ))}
                      </div>
                    </div>
                  )}
                </div>

                {/* Confidence & Signals */}
                <div style={{ marginBottom: 20 }}>
                  <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.08em', color: '#4c5a70', textTransform: 'uppercase', marginBottom: 10 }}>Confidence & Signals</div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 8 }}>
                    <MatchChip matchSource={selected.match_source} />
                    <span style={{ color: '#8892a4', fontSize: 12 }}>{confidenceLabel(selected.match_source)}</span>
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <div style={{ flex: 1, height: 4, background: '#1a2334', borderRadius: 9999, overflow: 'hidden' }}>
                      <div style={{
                        width: `${confidenceFromMatchSource(selected.match_source)}%`,
                        height: '100%',
                        background: confidenceFromMatchSource(selected.match_source) >= 80 ? '#2EE6D4' : '#e3b341',
                        borderRadius: 9999,
                      }} />
                    </div>
                    <span style={{ color: '#e2e8f0', fontSize: 13, fontWeight: 600, minWidth: 36 }}>
                      {confidenceFromMatchSource(selected.match_source)}%
                    </span>
                  </div>
                </div>

                {/* Triage buttons */}
                <div style={{ borderTop: '1px solid #1a2334', paddingTop: 16, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                  <button
                    className="btn"
                    disabled={patching}
                    onClick={() => applyStatus('shortlisted')}
                    style={{
                      background: selected.status === 'shortlisted' ? '#34d399' : 'rgba(52,211,153,0.1)',
                      color: selected.status === 'shortlisted' ? '#0f1623' : '#34d399',
                      border: '1px solid rgba(52,211,153,0.3)',
                      fontWeight: 600,
                    }}
                  >
                    ✓ Shortlist
                  </button>
                  <button
                    className="btn"
                    disabled={patching}
                    onClick={() => applyStatus('reviewed')}
                    style={{
                      background: selected.status === 'reviewed' ? '#e3b341' : 'rgba(227,179,65,0.1)',
                      color: selected.status === 'reviewed' ? '#0f1623' : '#e3b341',
                      border: '1px solid rgba(227,179,65,0.3)',
                      fontWeight: 600,
                    }}
                  >
                    Mark reviewed
                  </button>
                  <button
                    className="btn"
                    disabled={patching}
                    onClick={() => applyStatus('dismissed')}
                    style={{
                      background: selected.status === 'dismissed' ? '#f87171' : 'rgba(248,113,113,0.1)',
                      color: selected.status === 'dismissed' ? '#0f1623' : '#f87171',
                      border: '1px solid rgba(248,113,113,0.3)',
                      fontWeight: 600,
                    }}
                  >
                    Dismiss
                  </button>
                  {selected.status !== 'new' && (
                    <button
                      className="btn btn-ghost"
                      disabled={patching}
                      onClick={() => applyStatus('new')}
                      style={{ fontSize: 12 }}
                    >
                      Reset to new
                    </button>
                  )}
                </div>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
