import { useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from '../../router';
import { getComposerSession, resolveComposerRequirement, downloadComposerGapsBlob } from '../../api';
import type { ComposerSession } from '../../types';

function Donut({ done, total }: { done: number; total: number }) {
  const pct = total ? done / total : 0;
  const deg = Math.round(pct * 360);
  return (
    <div style={{ position: 'relative', width: 90, height: 90, flexShrink: 0 }}>
      <div style={{
        width: 90, height: 90, borderRadius: '50%',
        background: `conic-gradient(#34d399 0deg ${deg}deg, #1f2b40 ${deg}deg 360deg)`,
      }} />
      <div style={{
        position: 'absolute', inset: 12, borderRadius: '50%',
        background: 'rgba(227,179,65,0.07)',
        display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
      }}>
        <span className="mono" style={{ fontSize: 16, fontWeight: 700, color: '#cdd6e3', lineHeight: 1 }}>{done}/{total}</span>
        <span style={{ fontSize: 10, color: '#6b7990', marginTop: 2 }}>done</span>
      </div>
    </div>
  );
}

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

export default function GapsReport() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const pub = params.get('pub') ?? undefined;

  const [session, setSession] = useState<ComposerSession | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  function refresh() {
    if (!pub) { setLoading(false); return; }
    return getComposerSession(pub)
      .then(s => { setSession(s); setError(s ? '' : 'Failed to load this tender\'s Composer session'); })
      .finally(() => setLoading(false));
  }

  useEffect(() => { refresh(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [pub]);

  async function markResolved(id: number) {
    await resolveComposerRequirement(id);
    refresh();
  }

  async function handleDownloadGaps() {
    if (!pub) return;
    try {
      await triggerDownload(await downloadComposerGapsBlob(pub), 'gaps_report.txt');
    } catch { setError('No gaps report generated yet'); }
  }

  if (!pub) {
    return (
      <div className="card" style={{ padding: 40, textAlign: 'center', color: '#8a97ac' }}>
        Open Composer from a shortlisted tender in Portal → Pipeline to see its gaps report.
      </div>
    );
  }
  if (loading) return <div className="loading">Loading…</div>;
  if (!session) return <div className="error">{error || 'Could not load this Composer session'}</div>;

  const requirements = session.requirements;
  if (requirements.length === 0 || requirements.some(r => r.gap_status === null)) {
    return (
      <div className="card" style={{ padding: 40, textAlign: 'center', color: '#8a97ac' }}>
        No generated draft yet — generate one from Ingest &amp; Config first.
      </div>
    );
  }

  const total = requirements.length;
  const completed = requirements.filter(r => r.gap_status === 'completed');
  const linked = requirements.filter(r => r.gap_status === 'linked' && !r.resolved);
  const complete = requirements.filter(r => r.gap_status === 'complete');
  const outstanding = completed.length + linked.length;

  return (
    <div>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 6, flexWrap: 'wrap' }}>
        <h1 style={{ fontSize: 28, fontWeight: 700 }}>Gaps Report</h1>
        <span style={{ background: 'rgba(192,132,252,0.15)', color: '#c084fc', border: '1px solid rgba(192,132,252,0.3)', borderRadius: 6, fontSize: 11, fontWeight: 700, padding: '3px 8px' }}>COMPOSER</span>
      </div>
      <p style={{ color: '#8a97ac', marginBottom: 20, fontSize: 13 }}>
        The single number that matters: outstanding gaps. The proposal is not submission-ready until it reaches zero.
      </p>
      {error && <div style={{ color: '#f87171', fontSize: 13, marginBottom: 12 }}>{error}</div>}

      {/* Readiness card */}
      <div style={{
        background: outstanding === 0 ? 'rgba(52,211,153,0.07)' : 'rgba(227,179,65,0.07)',
        border: `1px solid ${outstanding === 0 ? 'rgba(52,211,153,0.3)' : 'rgba(227,179,65,0.3)'}`,
        borderRadius: 13, padding: '20px 24px', display: 'flex', alignItems: 'center', gap: 24, marginBottom: 24,
      }}>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.1em', color: '#6b7990', textTransform: 'uppercase', marginBottom: 8 }}>Submission status</div>
          {outstanding === 0 ? (
            <div style={{ fontSize: 36, fontWeight: 800, color: '#34d399', lineHeight: 1, marginBottom: 10 }}>SUBMISSION READY</div>
          ) : (
            <div style={{ fontSize: 36, fontWeight: 800, color: '#e3b341', lineHeight: 1, marginBottom: 10 }}>
              {outstanding} item{outstanding !== 1 ? 's' : ''} outstanding
            </div>
          )}
          <div style={{ fontSize: 13, color: '#8a97ac' }}>
            {completed.length} to be completed · {linked.length} to be linked · {complete.length} of {total} complete
          </div>
        </div>
        <Donut done={complete.length} total={total} />
      </div>

      {/* To be completed */}
      <div className="card" style={{ marginBottom: 16, overflow: 'hidden' }}>
        <div style={{ padding: '12px 18px', borderBottom: '1px solid #1f2b40', display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ width: 8, height: 8, borderRadius: '50%', background: '#e3b341', display: 'inline-block' }} />
          <span style={{ fontWeight: 600, fontSize: 14 }}>To be completed</span>
          <span style={{ color: '#8a97ac', fontSize: 12, marginLeft: 4 }}>no supporting documentation found</span>
          <span className="mono" style={{ marginLeft: 'auto', fontSize: 14, fontWeight: 700, color: '#e3b341' }}>{completed.length}</span>
        </div>
        {completed.length === 0 ? (
          <div style={{ padding: '16px 18px', color: '#34d399', fontSize: 13 }}>✓ Nothing to complete</div>
        ) : completed.map(req => (
          <div key={req.id} style={{ padding: '14px 18px', borderBottom: '1px solid #1b2536', display: 'flex', gap: 14, alignItems: 'flex-start' }}>
            <div style={{ flex: 1 }}>
              <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 4 }}>{req.title}</div>
              <div style={{ fontSize: 12, color: '#8a97ac' }}>No supporting documentation found. Add relevant tech_ documents and re-generate.</div>
            </div>
            <button className="btn btn-amber" style={{ flexShrink: 0, fontSize: 12 }} onClick={() => navigate(`/composer/ingest?pub=${encodeURIComponent(pub)}`)}>
              + Add document
            </button>
          </div>
        ))}
      </div>

      {/* To be linked */}
      <div className="card" style={{ marginBottom: 20, overflow: 'hidden' }}>
        <div style={{ padding: '12px 18px', borderBottom: '1px solid #1f2b40', display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ width: 8, height: 8, borderRadius: '50%', background: '#60a5fa', display: 'inline-block' }} />
          <span style={{ fontWeight: 600, fontSize: 14 }}>To be linked</span>
          <span className="mono" style={{ marginLeft: 'auto', fontSize: 14, fontWeight: 700, color: '#60a5fa' }}>{linked.length}</span>
        </div>
        {linked.length === 0 ? (
          <div style={{ padding: '16px 18px', color: '#34d399', fontSize: 13, display: 'flex', alignItems: 'center', gap: 8 }}>
            ✓ All links resolved
          </div>
        ) : linked.map(req => (
          <div key={req.id} style={{ padding: '14px 18px', borderBottom: '1px solid #1b2536', display: 'flex', gap: 14, alignItems: 'flex-start' }}>
            <div style={{ flex: 1 }}>
              <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 4 }}>{req.title}</div>
              {req.citations[0] && (
                <div style={{ fontSize: 12, color: '#60a5fa' }}>Closest match: <span className="mono">{req.citations[0].doc}</span></div>
              )}
            </div>
            <div style={{ display: 'flex', gap: 8, flexShrink: 0 }}>
              <button className="btn btn-blue" style={{ fontSize: 12 }} onClick={() => navigate(`/composer/review?pub=${encodeURIComponent(pub)}`)}>
                Link document
              </button>
              <button className="btn btn-ghost" style={{ fontSize: 12 }} onClick={() => markResolved(req.id)}>Mark resolved</button>
            </div>
          </div>
        ))}
      </div>

      <div>
        <button className="btn btn-ghost" onClick={handleDownloadGaps}>⤓ Download gaps_report.txt</button>
      </div>
    </div>
  );
}
