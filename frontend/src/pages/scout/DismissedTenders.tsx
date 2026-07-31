import { useEffect, useState } from 'react';
import { listTenders, patchTender } from '../../api';
import type { Tender } from '../../types';
import { formatDate, countryFlag, displayTagLine, displayDescription, hasTranslatedTagLine } from '../../utils';
import NoticeTypeBadge from '../../components/NoticeTypeBadge';

// CR-006 D4: reinstate reuses the same state-transition endpoint/states the
// New/Review Queue views use — no parallel path.
const REINSTATE_TARGETS: { status: string; label: string }[] = [
  { status: 'new', label: 'New' },
  { status: 'reviewed', label: 'Reviewed' },
  { status: 'shortlisted', label: 'Shortlisted' },
];

export default function DismissedTenders() {
  const [tenders, setTenders] = useState<Tender[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [selected, setSelected] = useState<Tender | null>(null);
  const [patching, setPatching] = useState(false);

  function load() {
    // CR-006 D1: newest-dismissed first — the API's own sort is deadline-based
    // and doesn't cover dismissed_at, so this sorts client-side.
    listTenders({ status: 'dismissed', limit: 1000 }).then(r => {
      const sorted = [...r.results].sort((a, b) => (b.dismissed_at || '').localeCompare(a.dismissed_at || ''));
      setTenders(sorted);
      setSelected(prev => {
        if (!prev) return sorted[0] ?? null;
        return sorted.find(t => t.pub_number === prev.pub_number) ?? sorted[0] ?? null;
      });
    }).catch(() => setError('Failed to load dismissed tenders'))
      .finally(() => setLoading(false));
  }

  useEffect(() => { load(); }, []);

  async function reinstate(status: string) {
    if (!selected || patching) return;
    setPatching(true);
    try {
      await patchTender(selected.pub_number, status);
      load();
    } finally {
      setPatching(false);
    }
  }

  if (loading) return <div className="loading">Loading…</div>;
  if (error) return <div className="error">{error}</div>;

  return (
    <div>
      <h1 style={{ fontSize: 28, fontWeight: 700, marginBottom: 4 }}>Dismissed</h1>
      <p style={{ color: '#8892a4', marginBottom: 16 }}>Tenders dismissed from review — reason, who, and when, with reinstatement</p>

      <div style={{ display: 'flex', gap: 10, marginBottom: 20, alignItems: 'center' }}>
        <span className="pill" style={{ background: 'rgba(248,113,113,0.1)', color: '#f87171', border: '1px solid rgba(248,113,113,0.3)' }}>
          ● {tenders.length} dismissed
        </span>
      </div>

      {tenders.length === 0 ? (
        <div className="card" style={{ padding: 32, textAlign: 'center', color: '#8892a4' }}>No dismissed tenders.</div>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, alignItems: 'flex-start' }}>
          {/* Left list */}
          <div className="card" style={{ maxHeight: '75vh', overflowY: 'auto' }}>
            <div style={{ padding: '12px 16px', borderBottom: '1px solid #1a2334', fontSize: 11, fontWeight: 700, letterSpacing: '0.08em', color: '#8892a4', textTransform: 'uppercase', position: 'sticky', top: 0, background: '#151d2c', zIndex: 1 }}>
              Dismissed · {tenders.length}
            </div>
            {tenders.map(t => {
              const isActive = selected?.pub_number === t.pub_number;
              const translated = hasTranslatedTagLine(t);
              return (
                <div
                  key={t.pub_number}
                  onClick={() => setSelected(t)}
                  style={{
                    padding: '12px 14px', cursor: 'pointer', borderBottom: '1px solid #1a2334',
                    background: isActive ? 'rgba(248,113,113,0.05)' : 'transparent',
                    borderLeft: `3px solid ${isActive ? '#f87171' : 'transparent'}`,
                  }}
                >
                  <div
                    title={translated ? `Original: ${t.tag_line}` : undefined}
                    style={{ fontWeight: 500, fontSize: 13, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: isActive ? '#e2e8f0' : '#c8d0de' }}
                  >
                    {translated && <span title="Translated from source language" style={{ marginRight: 4 }}>🌐</span>}
                    {displayTagLine(t, false)}
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 2 }}>
                    <span style={{ color: '#8892a4', fontSize: 11 }}>{t.source} · {t.country}</span>
                    <NoticeTypeBadge noticeType={t.notice_type} />
                  </div>
                  <div style={{ color: '#8892a4', fontSize: 11, marginTop: 4, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {t.dismissed_by && <>by {t.dismissed_by}</>}
                    {t.dismissed_by && t.dismissed_at && ' · '}
                    {t.dismissed_at && formatDate(t.dismissed_at)}
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
                  <NoticeTypeBadge noticeType={selected.notice_type} />
                  <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 10 }}>
                    {selected.url && (
                      <a href={selected.url} target="_blank" rel="noopener noreferrer"
                         className="btn btn-ghost" style={{ fontSize: 12, padding: '4px 10px' }}>
                        Open ↗
                      </a>
                    )}
                    <span style={{ background: 'rgba(0,0,0,0.2)', border: '1px solid #f87171', color: '#f87171', borderRadius: 9999, padding: '3px 10px', fontSize: 12, fontWeight: 500 }}>
                      ● Dismissed
                    </span>
                  </div>
                </div>
                <h2 style={{ fontSize: 20, fontWeight: 700, marginBottom: 20, lineHeight: 1.3 }}>
                  {displayTagLine(selected, false)}
                </h2>

                {selected.description && (
                  <p style={{ color: '#c8d0de', fontSize: 13, lineHeight: 1.6, marginBottom: 20 }}>
                    {displayDescription(selected, false)}
                  </p>
                )}

                {/* Core elements */}
                <div style={{ marginBottom: 20 }}>
                  <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.08em', color: '#4c5a70', textTransform: 'uppercase', marginBottom: 12 }}>Core Elements</div>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px 20px' }}>
                    <div>
                      <div style={{ fontSize: 10, fontWeight: 700, color: '#4c5a70', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 3 }}>Issuing Authority</div>
                      <div style={{ fontSize: 13 }}>{selected.buyer || '—'}</div>
                    </div>
                    <div>
                      <div style={{ fontSize: 10, fontWeight: 700, color: '#4c5a70', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 3 }}>CPV Codes</div>
                      <div style={{ fontSize: 13 }}>{selected.cpv_codes?.length ? Array.from(new Set(selected.cpv_codes)).join(', ') : '—'}</div>
                    </div>
                    <div>
                      <div style={{ fontSize: 10, fontWeight: 700, color: '#4c5a70', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 3 }}>Deadline</div>
                      <div className="mono" style={{ fontSize: 13 }}>{formatDate(selected.deadline)}</div>
                    </div>
                  </div>
                </div>

                {/* CR-006 D2/D3: dismissal metadata — reason, who, when */}
                <div style={{ padding: 12, background: 'rgba(248,113,113,0.05)', border: '1px solid rgba(248,113,113,0.2)', borderRadius: 8, marginBottom: 20 }}>
                  <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.08em', color: '#4c5a70', textTransform: 'uppercase', marginBottom: 6 }}>
                    Dismissal reason
                  </div>
                  <div style={{ fontSize: 13, color: '#c8d0de', marginBottom: 8 }}>{selected.dismissal_reason || '—'}</div>
                  <div style={{ fontSize: 11, color: '#8892a4' }}>
                    {selected.dismissed_by && <>Dismissed by {selected.dismissed_by}</>}
                    {selected.dismissed_by && selected.dismissed_at && ' · '}
                    {selected.dismissed_at && formatDate(selected.dismissed_at)}
                  </div>
                </div>

                {/* CR-006 D4: reinstate — same state-transition mechanism as Review Queue */}
                <div style={{ borderTop: '1px solid #1a2334', paddingTop: 16 }}>
                  <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.08em', color: '#4c5a70', textTransform: 'uppercase', marginBottom: 10 }}>
                    Reinstate as
                  </div>
                  <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                    {REINSTATE_TARGETS.map(target => (
                      <button
                        key={target.status}
                        className="btn"
                        disabled={patching}
                        onClick={() => reinstate(target.status)}
                        style={{
                          background: 'rgba(52,211,153,0.1)', color: '#34d399',
                          border: '1px solid rgba(52,211,153,0.3)', fontWeight: 600,
                        }}
                      >
                        {target.label}
                      </button>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
