import { useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from '../../router';
import {
  getComposerSession, regenerateComposerSection, searchVault,
  downloadComposerProposalBlob, downloadComposerMatrixBlob,
} from '../../api';
import type { ComposerRequirement, ComposerSession, VaultSearchResult } from '../../types';

type GapStatus = NonNullable<ComposerRequirement['gap_status']>;

const STATUS_COLORS: Record<GapStatus, string> = {
  complete: '#34d399',
  linked: '#60a5fa',
  completed: '#e3b341',
};

const STATUS_LABELS: Record<GapStatus, string> = {
  complete: 'Complete',
  linked: 'To be linked',
  completed: 'To be completed',
};

// Sort order is fixed amber-first regardless of the active filter — the
// worst gaps always surface at the top of the list.
const SORT_RANK: Record<GapStatus, number> = { completed: 0, linked: 1, complete: 2 };

type Filter = 'all' | GapStatus;

async function triggerDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export default function ProposalReview() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const pub = params.get('pub') ?? undefined;

  const [session, setSession] = useState<ComposerSession | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [filter, setFilter] = useState<Filter>('all');
  const [refineText, setRefineText] = useState('');
  const [regenerating, setRegenerating] = useState(false);
  const [showHistory, setShowHistory] = useState(false);

  // CR-004 F3 — Composer -> Vault "Source materials" panel.
  const [vaultQuery, setVaultQuery] = useState('');
  const [vaultCpv, setVaultCpv] = useState('');
  const [vaultResults, setVaultResults] = useState<VaultSearchResult[]>([]);
  const [vaultSearching, setVaultSearching] = useState(false);
  const [selectedVaultDocIds, setSelectedVaultDocIds] = useState<number[]>([]);

  function refresh() {
    if (!pub) { setLoading(false); return; }
    return getComposerSession(pub)
      .then(s => {
        setSession(s);
        setError(s ? '' : 'Failed to load this tender\'s Composer session');
        if (s) {
          setSelectedId(prev => {
            if (prev != null && s.requirements.some(r => r.id === prev)) return prev;
            const firstGap = s.requirements.find(r => r.gap_status === 'completed' || r.gap_status === 'linked');
            return (firstGap ?? s.requirements[0])?.id ?? null;
          });
        }
      })
      .finally(() => setLoading(false));
  }

  useEffect(() => { refresh(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [pub]);

  const generating = (session?.requirements.length ?? 0) > 0
    && session!.requirements.some(r => r.gap_status === null);
  useEffect(() => {
    if (!pub || !generating) return;
    const handle = setInterval(refresh, 3000);
    return () => clearInterval(handle);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pub, generating]);

  useEffect(() => {
    setRefineText(''); setShowHistory(false);
    setVaultResults([]); setSelectedVaultDocIds([]); setVaultQuery(''); setVaultCpv('');
  }, [selectedId]);

  if (!pub) {
    return (
      <div className="card" style={{ padding: 40, textAlign: 'center', color: '#8a97ac' }}>
        Open Composer from a shortlisted tender in Portal → Pipeline to review a proposal draft.
      </div>
    );
  }
  if (loading) return <div className="loading">Loading…</div>;
  if (!session) return <div className="error">{error || 'Could not load this Composer session'}</div>;

  const requirements = session.requirements;
  if (generating) {
    return (
      <div className="card" style={{ padding: 40, textAlign: 'center', color: '#e3b341' }}>
        ⏳ Generating the draft — this can take a few minutes for a large requirement set.
      </div>
    );
  }
  if (requirements.length === 0) {
    return (
      <div className="card" style={{ padding: 40, textAlign: 'center', color: '#8a97ac' }}>
        No draft yet — validate every requirement on Ingest &amp; Config, then generate a draft.
      </div>
    );
  }

  const counts = {
    completed: requirements.filter(r => r.gap_status === 'completed').length,
    linked: requirements.filter(r => r.gap_status === 'linked').length,
    complete: requirements.filter(r => r.gap_status === 'complete').length,
  };
  const outstanding = counts.completed + counts.linked;
  const sorted = [...requirements].sort((a, b) =>
    SORT_RANK[(a.gap_status ?? 'completed')] - SORT_RANK[(b.gap_status ?? 'completed')]);
  const filtered = filter === 'all' ? sorted : sorted.filter(r => r.gap_status === filter);
  const selected = requirements.find(r => r.id === selectedId) ?? requirements[0];

  const filterChips: { key: Filter; label: string }[] = [
    { key: 'all', label: `All · ${requirements.length}` },
    { key: 'completed', label: `To complete · ${counts.completed}` },
    { key: 'linked', label: `To link · ${counts.linked}` },
    { key: 'complete', label: `Complete · ${counts.complete}` },
  ];

  async function handleRegenerate() {
    if (!pub || !refineText.trim() || !selected) return;
    setRegenerating(true);
    try {
      await regenerateComposerSection(pub, selected.id, refineText.trim(), selectedVaultDocIds);
      setRefineText('');
      setSelectedVaultDocIds([]);
      setVaultResults([]);
      await refresh();
    } finally {
      setRegenerating(false);
    }
  }

  async function handleVaultSearch() {
    if (!vaultQuery.trim() && !vaultCpv.trim()) return;
    setVaultSearching(true);
    try {
      const res = await searchVault({ query: vaultQuery.trim() || undefined, cpv: vaultCpv.trim() || undefined });
      setVaultResults(res.results);
    } finally {
      setVaultSearching(false);
    }
  }

  function toggleVaultDoc(docId: number) {
    setSelectedVaultDocIds(prev => prev.includes(docId) ? prev.filter(id => id !== docId) : [...prev, docId]);
  }

  async function handleDownloadDocx() {
    if (!pub) return;
    try {
      await triggerDownload(await downloadComposerProposalBlob(pub), 'technical_proposal.docx');
    } catch { setError('No proposal generated yet'); }
  }

  async function handleDownloadMatrix() {
    if (!pub) return;
    try {
      await triggerDownload(await downloadComposerMatrixBlob(pub), 'matrix_filled.xlsx');
    } catch { setError('No filled matrix available'); }
  }

  return (
    <div>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 6, flexWrap: 'wrap' }}>
        <h1 style={{ fontSize: 28, fontWeight: 700 }}>Proposal Review</h1>
        <span style={{ background: 'rgba(192,132,252,0.15)', color: '#c084fc', border: '1px solid rgba(192,132,252,0.3)', borderRadius: 6, fontSize: 11, fontWeight: 700, padding: '3px 8px' }}>COMPOSER</span>
      </div>
      <p style={{ color: '#8a97ac', marginBottom: 16, fontSize: 13 }}>
        Every response traces to the SOW. Review section by section, refine the prose, and clear the gaps before submission.
      </p>
      {error && <div style={{ color: '#f87171', fontSize: 13, marginBottom: 12 }}>{error}</div>}

      {/* Readiness banner */}
      <div style={{
        background: outstanding === 0 ? 'rgba(52,211,153,0.07)' : 'rgba(227,179,65,0.07)',
        border: `1px solid ${outstanding === 0 ? 'rgba(52,211,153,0.3)' : 'rgba(227,179,65,0.3)'}`,
        borderRadius: 10, padding: '16px 20px', marginBottom: 20, display: 'flex', alignItems: 'center', gap: 24, flexWrap: 'wrap',
      }}>
        <div style={{ flex: 1, minWidth: 200 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
            <span style={{ width: 8, height: 8, borderRadius: '50%', background: outstanding === 0 ? '#34d399' : '#e3b341', display: 'inline-block', flexShrink: 0 }} />
            <span style={{ color: outstanding === 0 ? '#34d399' : '#e3b341', fontWeight: 800, fontSize: 14 }}>
              {outstanding === 0 ? 'SUBMISSION READY' : 'NOT SUBMISSION-READY'}
            </span>
          </div>
          <div style={{ color: '#8a97ac', fontSize: 12 }}>
            Drafting <strong style={{ color: '#cdd6e3' }}>{session.tender_title}</strong> · {session.source} · deadline {session.deadline || '—'}
          </div>
        </div>
        <div style={{ display: 'flex', gap: 0, flexShrink: 0 }}>
          {[
            { count: counts.completed, label: 'TO COMPLETE', color: '#e3b341' },
            { count: counts.linked, label: 'TO LINK', color: '#60a5fa' },
            { count: counts.complete, label: 'COMPLETE', color: '#34d399' },
          ].map((stat, i) => (
            <div key={stat.label} style={{ textAlign: 'center', padding: '0 20px', borderLeft: i > 0 ? '1px solid #222e44' : 'none' }}>
              <div className="mono" style={{ fontSize: 28, fontWeight: 700, color: stat.color, lineHeight: 1 }}>{stat.count}</div>
              <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.07em', color: '#6b7990', marginTop: 4 }}>{stat.label}</div>
            </div>
          ))}
        </div>
        <div style={{ display: 'flex', gap: 8, flexShrink: 0 }}>
          <button className="btn btn-amber" onClick={() => navigate(`/composer/gaps?pub=${encodeURIComponent(pub)}`)}>View gaps →</button>
          <button className="btn btn-ghost" onClick={handleDownloadDocx}>⤓ .docx</button>
          {session.matrix && <button className="btn btn-ghost" onClick={handleDownloadMatrix}>⤓ matrix .xlsx</button>}
        </div>
      </div>

      {/* Master / detail */}
      <div style={{ display: 'grid', gridTemplateColumns: '340px 1fr', gap: 20 }}>
        {/* Left — list */}
        <div className="card" style={{ padding: 0, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
          <div style={{ padding: '12px 14px', borderBottom: '1px solid #1f2b40', display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {filterChips.map(f => (
              <button
                key={f.key}
                onClick={() => setFilter(f.key)}
                style={{
                  border: filter === f.key ? 'none' : '1px solid #222e44',
                  background: filter === f.key ? '#c084fc' : 'transparent',
                  color: filter === f.key ? '#0f1623' : '#8a97ac',
                  borderRadius: 6, padding: '4px 10px', fontSize: 12, fontWeight: 500, cursor: 'pointer',
                }}
              >
                {f.label}
              </button>
            ))}
          </div>
          <div style={{ overflowY: 'auto', maxHeight: 560 }}>
            {filtered.map(req => {
              const isActive = req.id === selectedId;
              const status = req.gap_status ?? 'completed';
              const color = STATUS_COLORS[status];
              return (
                <div
                  key={req.id}
                  onClick={() => setSelectedId(req.id)}
                  style={{
                    padding: '12px 14px', borderBottom: '1px solid #1b2536', cursor: 'pointer',
                    borderLeft: isActive ? '3px solid #c084fc' : '3px solid transparent',
                    background: isActive ? 'rgba(192,132,252,0.06)' : 'transparent',
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                    <span style={{ width: 8, height: 8, borderRadius: '50%', background: color, display: 'inline-block', flexShrink: 0 }} />
                    <span style={{ fontSize: 13, fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{req.title}</span>
                  </div>
                  <div style={{ marginLeft: 16, display: 'flex', gap: 8, alignItems: 'center' }}>
                    <span style={{ fontSize: 11, color }}>{STATUS_LABELS[status]}</span>
                    <span className="mono" style={{ fontSize: 11, color: '#6b7990' }}>sim {(req.similarity ?? 0).toFixed(2)}</span>
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {/* Right — detail */}
        {selected && (
          <div className="card" style={{ padding: '20px 22px', display: 'flex', flexDirection: 'column', gap: 18, alignSelf: 'start' }}>
            {(() => {
              const status = selected.gap_status ?? 'completed';
              const color = STATUS_COLORS[status];
              return (
                <>
                  <div style={{ display: 'flex', alignItems: 'flex-start', gap: 16 }}>
                    <div style={{ flex: 1, minWidth: 200 }}>
                      <div className="mono" style={{ fontSize: 11, color: '#6b7990', marginBottom: 6 }}>Requirement</div>
                      <div style={{ fontSize: 20, fontWeight: 700, lineHeight: 1.3 }}>{selected.title}</div>
                    </div>
                    <div style={{ flexShrink: 0, textAlign: 'right' }}>
                      <div style={{
                        background: color + '1a', border: `1px solid ${color}44`, color,
                        borderRadius: 7, padding: '4px 10px', fontSize: 12, fontWeight: 600, marginBottom: 6, display: 'inline-block',
                      }}>
                        {STATUS_LABELS[status]}
                      </div>
                      <div className="mono" style={{ fontSize: 28, fontWeight: 700, color, lineHeight: 1 }}>{(selected.similarity ?? 0).toFixed(2)}</div>
                      <div style={{ fontSize: 10, color: '#6b7990', textTransform: 'uppercase', letterSpacing: '0.08em', marginTop: 4 }}>SIMILARITY</div>
                    </div>
                  </div>

                  <div>
                    <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.08em', color: '#6b7990', textTransform: 'uppercase', marginBottom: 8 }}>Requirement · from SOW</div>
                    <div style={{ borderLeft: '3px solid rgba(136,146,164,0.3)', background: 'rgba(136,146,164,0.08)', padding: '10px 14px', borderRadius: '0 6px 6px 0', fontSize: 13, color: '#cdd6e3', lineHeight: 1.6 }}>
                      {selected.extracted}
                    </div>
                  </div>

                  <div>
                    <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.08em', color: '#6b7990', textTransform: 'uppercase', marginBottom: 8 }}>Generated response</div>
                    {status === 'completed' ? (
                      <div style={{ border: '1.5px dashed rgba(248,113,113,0.4)', background: 'rgba(248,113,113,0.06)', padding: 14, borderRadius: 8 }}>
                        <div style={{ color: '#f87171', fontWeight: 700, marginBottom: 6 }}>⛔ To be completed</div>
                        <div style={{ color: '#8a97ac', fontSize: 13 }}>No supporting technical documentation found. Add relevant tech_ documents and re-generate.</div>
                      </div>
                    ) : (
                      <>
                        <div style={{ fontSize: 13, color: '#cdd6e3', lineHeight: 1.6, marginBottom: status === 'linked' ? 10 : 0 }}>{selected.response}</div>
                        {status === 'linked' && selected.citations[0] && (
                          <div style={{ background: 'rgba(96,165,250,0.08)', border: '1px solid rgba(96,165,250,0.25)', borderRadius: 8, padding: '10px 14px', fontSize: 12, color: '#60a5fa', marginTop: 10 }}>
                            🔗 To be linked — <span className="mono">{selected.citations[0].doc}</span> awaiting formal linking
                          </div>
                        )}
                      </>
                    )}
                  </div>

                  {selected.citations.length > 0 && (
                    <div>
                      <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.08em', color: '#6b7990', textTransform: 'uppercase', marginBottom: 8 }}>Source citations · from Vault</div>
                      {selected.citations.map((c, i) => (
                        <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 0', borderBottom: '1px solid #1b2536' }}>
                          <span style={{ color: '#6b7990' }}>↳</span>
                          <span className="mono" style={{ fontSize: 12, color: '#cdd6e3', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.doc}</span>
                          <span className="mono" style={{ fontSize: 12, color: '#2EE6D4', flexShrink: 0 }}>{c.score.toFixed(2)}</span>
                        </div>
                      ))}
                    </div>
                  )}

                  <div>
                    <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.08em', color: '#6b7990', textTransform: 'uppercase', marginBottom: 8 }}>Refine this section</div>
                    <textarea
                      className="input-field"
                      style={{ minHeight: 72, resize: 'vertical', marginBottom: 10 }}
                      placeholder="Type a refinement instruction — e.g. 'reference the SGS certificate' / 'be more assertive' / 'make this shorter'"
                      value={refineText}
                      onChange={e => setRefineText(e.target.value)}
                    />
                    <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                      <button className="btn btn-purple-solid" style={{ fontWeight: 600 }} disabled={regenerating || !refineText.trim()} onClick={handleRegenerate}>
                        {regenerating ? 'Regenerating…' : '⟳ Regenerate section'}
                      </button>
                      <span style={{ color: '#6b7990', fontSize: 12 }}>v{selected.version}</span>
                      {selected.version_history.length > 0 && (
                        <span style={{ color: '#8a97ac', fontSize: 12, cursor: 'pointer' }} onClick={() => setShowHistory(v => !v)}>
                          {showHistory ? '⌃' : '⌄'} Version history ({selected.version_history.length})
                        </span>
                      )}
                    </div>
                    {showHistory && (
                      <div style={{ marginTop: 12, display: 'flex', flexDirection: 'column', gap: 8 }}>
                        {[...selected.version_history].reverse().map((v, i) => (
                          <div key={i} style={{ background: '#121a28', border: '1px solid #1f2b40', borderRadius: 6, padding: 10, fontSize: 12 }}>
                            <div style={{ color: '#8a97ac', marginBottom: 4 }}>Feedback: "{v.feedback}"</div>
                            <div style={{ color: '#cdd6e3' }}>{v.text}</div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>

                  {/* CR-004 F3 — Composer -> Vault: search the Vault library and pull
                      specific documents into this section's next regenerate. */}
                  <div style={{ borderTop: '1px solid #1f2b40', paddingTop: 16 }}>
                    <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.08em', color: '#6b7990', textTransform: 'uppercase', marginBottom: 8 }}>
                      Source materials · search Vault
                    </div>
                    <div style={{ display: 'flex', gap: 8, marginBottom: 10, flexWrap: 'wrap' }}>
                      <input
                        className="input-field" style={{ flex: 2, minWidth: 160 }}
                        placeholder="Search by material, spec…"
                        value={vaultQuery}
                        onChange={e => setVaultQuery(e.target.value)}
                        onKeyDown={e => { if (e.key === 'Enter') handleVaultSearch(); }}
                      />
                      <input
                        className="input-field mono" style={{ flex: 1, minWidth: 100 }}
                        placeholder="CPV code"
                        value={vaultCpv}
                        onChange={e => setVaultCpv(e.target.value)}
                        onKeyDown={e => { if (e.key === 'Enter') handleVaultSearch(); }}
                      />
                      <button className="btn btn-ghost" onClick={handleVaultSearch} disabled={vaultSearching}>
                        {vaultSearching ? 'Searching…' : '🔍 Search'}
                      </button>
                    </div>
                    {vaultResults.length > 0 && (
                      <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginBottom: 10 }}>
                        {vaultResults.map(r => {
                          const checked = selectedVaultDocIds.includes(r.doc_id);
                          return (
                            <label
                              key={r.doc_id}
                              style={{
                                display: 'flex', alignItems: 'flex-start', gap: 10, padding: '8px 10px',
                                borderRadius: 6, cursor: 'pointer',
                                background: checked ? 'rgba(96,165,250,0.08)' : '#121a28',
                                border: `1px solid ${checked ? 'rgba(96,165,250,0.35)' : '#1f2b40'}`,
                              }}
                            >
                              <input type="checkbox" checked={checked} onChange={() => toggleVaultDoc(r.doc_id)} style={{ marginTop: 2 }} />
                              <div style={{ flex: 1, minWidth: 0 }}>
                                <div className="mono" style={{ fontSize: 12, color: '#cdd6e3', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                  {r.filename}
                                  {r.similarity != null && <span style={{ color: '#2EE6D4', marginLeft: 8 }}>sim {r.similarity.toFixed(2)}</span>}
                                </div>
                                {r.text && (
                                  <div style={{ fontSize: 11, color: '#8a97ac', marginTop: 3, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                    {r.text}
                                  </div>
                                )}
                              </div>
                            </label>
                          );
                        })}
                      </div>
                    )}
                    {selectedVaultDocIds.length > 0 && (
                      <div style={{ fontSize: 12, color: '#60a5fa', marginBottom: 4 }}>
                        {selectedVaultDocIds.length} Vault document{selectedVaultDocIds.length === 1 ? '' : 's'} will be cited on the next regenerate.
                      </div>
                    )}
                  </div>
                </>
              );
            })()}
          </div>
        )}
      </div>
    </div>
  );
}
