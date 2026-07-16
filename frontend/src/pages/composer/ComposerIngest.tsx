import { useEffect, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from '../../router';
import {
  getComposerSession, uploadComposerDocument, uploadComposerMatrix,
  triggerComposerEnrich, triggerComposerInterpret, patchRequirement, postGenerate,
} from '../../api';
import type { ComposerSession, ComposerDoc } from '../../types';

const DOC_ROLES: Record<ComposerDoc['role'], { label: string; color: string }> = {
  sow: { label: 'SOW', color: '#60a5fa' },
  tech: { label: 'TECH', color: '#e3b341' },
  background: { label: 'BG', color: '#2EE6D4' },
  parta: { label: 'PART A', color: '#c084fc' },
  example: { label: 'EXAMPLE', color: '#8a97ac' },
  unknown: { label: '?', color: '#6b7990' },
};

function RoleTag({ role }: { role: ComposerDoc['role'] }) {
  const info = DOC_ROLES[role];
  return (
    <span style={{
      background: info.color + '1a',
      color: info.color,
      border: `1px solid ${info.color}44`,
      borderRadius: 5, fontSize: 10, fontWeight: 700,
      padding: '2px 6px', letterSpacing: '0.05em', whiteSpace: 'nowrap', flexShrink: 0,
    }}>
      {info.label}
    </span>
  );
}

function Stepper({ hasDocs, hasReqs, allValidated }: { hasDocs: boolean; hasReqs: boolean; allValidated: boolean }) {
  const steps = [
    { num: 1, label: 'Ingest', state: hasDocs ? 'done' : 'active' },
    { num: 2, label: 'Interpret', state: hasReqs ? 'done' : hasDocs ? 'active' : 'locked' },
    { num: 3, label: 'Validate', state: allValidated ? 'done' : hasReqs ? 'active' : 'locked' },
    { num: 4, label: 'Generate draft', state: allValidated ? 'active' : 'locked' },
  ] as const;

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 0, marginBottom: 24, background: '#151d2c', border: '1px solid #222e44', borderRadius: 10, padding: '14px 20px' }}>
      {steps.map((step, i) => (
        <div key={step.num} style={{ display: 'flex', alignItems: 'center', gap: 8, flex: i < steps.length - 1 ? 'none' : 1 }}>
          <div style={{
            width: 26, height: 26, borderRadius: '50%', display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontSize: 12, fontWeight: 700, flexShrink: 0,
            ...(step.state === 'done' ? { background: '#c084fc', color: '#0f1623' } :
               step.state === 'active' ? { background: '#e3b341', color: '#0f1623' } :
               { background: '#1f2b40', color: '#6b7990' }),
          }}>
            {step.state === 'done' ? '✓' : step.num}
          </div>
          <span style={{
            fontSize: 13, fontWeight: step.state === 'active' ? 600 : 400,
            color: step.state === 'done' ? '#c084fc' : step.state === 'active' ? '#e3b341' : '#6b7990',
          }}>
            {step.label}
          </span>
          {i < steps.length - 1 && (
            <span style={{ color: '#3a4a66', margin: '0 12px', fontSize: 16 }}>→</span>
          )}
        </div>
      ))}
    </div>
  );
}

export default function ComposerIngest() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const pub = params.get('pub') ?? undefined;

  const [session, setSession] = useState<ComposerSession | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [uploading, setUploading] = useState(false);
  const [interpreting, setInterpreting] = useState(false);
  const [enriching, setEnriching] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const matrixInputRef = useRef<HTMLInputElement>(null);

  function refresh() {
    if (!pub) { setLoading(false); return; }
    return getComposerSession(pub)
      .then(s => { setSession(s); setError(s ? '' : 'Failed to load this tender\'s Composer session'); })
      .finally(() => setLoading(false));
  }

  useEffect(() => { refresh(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [pub]);

  // Poll while anything is still processing — document ingest, enrichment,
  // requirement interpretation, or a generate run (all background tasks).
  const anyDocsProcessing = session?.docs.some(d => d.status === 'processing') ?? false;
  const anyReqsUngenerated = (session?.requirements.length ?? 0) > 0
    && session!.requirements.every(r => r.validation === 'validated')
    && session!.requirements.some(r => r.gap_status === null);
  useEffect(() => {
    if (!pub || (!anyDocsProcessing && !interpreting && !enriching && !anyReqsUngenerated)) return;
    const handle = setInterval(refresh, 3000);
    return () => clearInterval(handle);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pub, anyDocsProcessing, interpreting, enriching, anyReqsUngenerated]);

  async function handleFilesSelected(files: FileList | null) {
    if (!files || files.length === 0 || !pub) return;
    setUploading(true);
    setError('');
    try {
      for (const file of Array.from(files)) {
        await uploadComposerDocument(pub, file);
      }
      await refresh();
    } catch {
      setError('Upload failed — try again');
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  }

  async function handleMatrixSelected(files: FileList | null) {
    const file = files?.[0];
    if (!file || !pub) return;
    setError('');
    try {
      await uploadComposerMatrix(pub, file);
      await refresh();
    } catch {
      setError('Could not parse compliance matrix — check the column layout');
    } finally {
      if (matrixInputRef.current) matrixInputRef.current.value = '';
    }
  }

  async function handleEnrich() {
    if (!pub) return;
    setEnriching(true);
    try {
      await triggerComposerEnrich(pub);
    } finally {
      setTimeout(() => setEnriching(false), 3000); // give the poll loop a window to pick up progress
    }
  }

  async function handleInterpret() {
    if (!pub) return;
    setInterpreting(true);
    try {
      await triggerComposerInterpret(pub);
      setTimeout(refresh, 3000);
    } finally {
      setTimeout(() => setInterpreting(false), 3000);
    }
  }

  async function toggle(id: number, current: 'pending' | 'validated' | 'flagged', next: 'validated' | 'flagged') {
    const target = current === next ? 'pending' : next;
    await patchRequirement(id, target);
    refresh();
  }

  async function handleGenerate() {
    if (!pub) return;
    await postGenerate(pub);
    navigate(`/composer/review?pub=${encodeURIComponent(pub)}`);
  }

  if (!pub) {
    return (
      <div className="card" style={{ padding: 40, textAlign: 'center', color: '#8a97ac' }}>
        Open Composer from a shortlisted tender in Portal → Pipeline to start drafting a proposal.
      </div>
    );
  }
  if (loading) return <div className="loading">Loading…</div>;
  if (!session) return <div className="error">{error || 'Could not load this Composer session'}</div>;

  const docs = session.docs;
  const requirements = session.requirements;
  const validatedCount = requirements.filter(r => r.validation === 'validated').length;
  const total = requirements.length;
  const allValidated = total > 0 && validatedCount === total;
  const imageHeavyDocs = docs.filter(d => d.image_heavy);

  return (
    <div>
      {/* Page header */}
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 6, flexWrap: 'wrap' }}>
        <h1 style={{ fontSize: 28, fontWeight: 700 }}>Ingest & Config</h1>
        <span style={{ background: 'rgba(192,132,252,0.15)', color: '#c084fc', border: '1px solid rgba(192,132,252,0.3)', borderRadius: 6, fontSize: 11, fontWeight: 700, padding: '3px 8px', letterSpacing: '0.06em' }}>
          COMPOSER
        </span>
      </div>
      <p style={{ color: '#8a97ac', marginBottom: 20, fontSize: 13 }}>
        Drafting <strong style={{ color: '#cdd6e3' }}>{session.tender_title}</strong> · {session.source}
        {' · '}Drop in the tender documents — Composer reads and interprets them, then the responsible person validates each requirement before any draft is generated
      </p>

      {error && <div style={{ color: '#f87171', fontSize: 13, marginBottom: 12 }}>{error}</div>}

      <Stepper hasDocs={docs.length > 0} hasReqs={total > 0} allValidated={allValidated} />

      {/* 2-column grid */}
      <div style={{ display: 'grid', gridTemplateColumns: '300px 1fr', gap: 24 }}>
        {/* Left column */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          {/* Drop zone */}
          <div
            onClick={() => fileInputRef.current?.click()}
            style={{
              border: '1.5px dashed rgba(192,132,252,0.35)',
              background: 'rgba(192,132,252,0.04)',
              borderRadius: 10, padding: '28px 20px',
              textAlign: 'center', cursor: uploading ? 'default' : 'pointer',
            }}
          >
            <input
              ref={fileInputRef} type="file" accept=".pdf,.docx" multiple
              style={{ display: 'none' }} disabled={uploading}
              onChange={e => handleFilesSelected(e.target.files)}
            />
            <div style={{ fontSize: 28, color: '#c084fc', marginBottom: 10 }}>⤓</div>
            <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 8 }}>
              {uploading ? 'Uploading…' : 'Drop tender documents'}
            </div>
            <div style={{ color: '#8a97ac', fontSize: 12, lineHeight: 1.6 }}>
              Role auto-detected from filename prefix —{' '}
              <span className="mono" style={{ color: '#cdd6e3', fontSize: 11 }}>sow_ · tech_ · background_ · parta_ · example_</span>
            </div>
          </div>

          {/* Document library */}
          <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '12px 14px', borderBottom: '1px solid #1f2b40' }}>
              <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.1em', color: '#6b7990', textTransform: 'uppercase' }}>Document library</span>
              <span style={{ color: '#8a97ac', fontSize: 12 }}>{docs.length} files</span>
            </div>
            {docs.length === 0 ? (
              <div style={{ padding: '20px 14px', textAlign: 'center', color: '#8a97ac', fontSize: 12 }}>
                No documents yet.
              </div>
            ) : docs.map(doc => (
              <div key={doc.id} style={{ padding: '10px 14px', borderBottom: '1px solid #1b2536' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: doc.image_heavy ? 4 : 0 }}>
                  <RoleTag role={doc.role} />
                  <span className="mono" style={{ fontSize: 11, color: '#cdd6e3', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {doc.filename}
                  </span>
                </div>
                <div style={{ marginLeft: 48, fontSize: 11, color: '#8a97ac', marginBottom: doc.image_heavy ? 4 : 0 }}>
                  {doc.status !== 'style_only' && doc.pages != null ? `${doc.pages} pages · ${doc.chunks ?? 0} chunks` : ''}
                  {' '}
                  {doc.status === 'ingested' && <><span className="dot-live" style={{ width: 6, height: 6, marginRight: 4 }} /><span style={{ color: '#34d399' }}>Ingested</span></>}
                  {doc.status === 'processing' && <><span className="dot-paused" style={{ width: 6, height: 6, marginRight: 4 }} /><span style={{ color: '#e3b341' }}>Processing</span></>}
                  {doc.status === 'style_only' && <><span style={{ color: '#8a97ac' }}>◌ Style only</span></>}
                </div>
                {doc.image_heavy && (
                  <div style={{ marginLeft: 48, marginTop: 4, fontSize: 11, color: '#e3b341', background: 'rgba(227,179,65,0.08)', border: '1px solid rgba(227,179,65,0.2)', borderRadius: 5, padding: '3px 8px' }}>
                    ⚠ Image-heavy PDF — run datasheet enrichment to extract specs
                  </div>
                )}
              </div>
            ))}
            {imageHeavyDocs.length > 0 && (
              <div style={{ padding: '10px 14px' }}>
                <button className="btn btn-amber" style={{ fontSize: 12 }} disabled={enriching} onClick={handleEnrich}>
                  {enriching ? 'Enriching…' : `✨ Enrich datasheets (${imageHeavyDocs.length})`}
                </button>
              </div>
            )}
          </div>

          {/* Compliance matrix */}
          <div className="card" style={{ padding: '12px 14px', display: 'flex', alignItems: 'center', gap: 10 }}>
            <span style={{ fontSize: 18, color: '#8a97ac' }}>▦</span>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div className="mono" style={{ fontSize: 12, color: '#cdd6e3', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {session.matrix ? session.matrix.filename : 'compliance_matrix.xlsx'}
              </div>
              <div style={{ fontSize: 11, color: '#8a97ac' }}>
                {session.matrix ? `Compliance matrix · ${session.matrix.requirement_count} requirements` : 'Optional — used to fill a compliance matrix export'}
              </div>
            </div>
            {session.matrix ? (
              <span className="pill pill-green" style={{ fontSize: 11, whiteSpace: 'nowrap' }}>
                <span className="dot-live" style={{ width: 6, height: 6 }} /> Loaded
              </span>
            ) : (
              <>
                <input ref={matrixInputRef} type="file" accept=".xlsx" style={{ display: 'none' }}
                       onChange={e => handleMatrixSelected(e.target.files)} />
                <button className="btn btn-ghost" style={{ fontSize: 12 }} onClick={() => matrixInputRef.current?.click()}>
                  + Upload
                </button>
              </>
            )}
          </div>
        </div>

        {/* Right column — Interpreted requirements */}
        <div className="card" style={{ padding: 0, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
          <div style={{ padding: '14px 16px', borderBottom: '1px solid #1f2b40' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
              <span style={{ fontWeight: 600, fontSize: 14 }}>Interpreted requirements</span>
              <span style={{ color: '#8a97ac', fontSize: 12 }}>{validatedCount} of {total} validated</span>
            </div>
            <div style={{ height: 4, background: '#1f2b40', borderRadius: 9999, overflow: 'hidden' }}>
              <div style={{ height: '100%', width: total ? `${(validatedCount / total) * 100}%` : '0%', background: '#c084fc', borderRadius: 9999, transition: 'width 0.3s' }} />
            </div>
          </div>

          <div style={{ flex: 1, overflowY: 'auto' }}>
            {total === 0 ? (
              <div style={{ padding: 32, textAlign: 'center' }}>
                <div style={{ color: '#8a97ac', fontSize: 13, marginBottom: 14 }}>
                  {docs.some(d => d.role === 'sow') ? 'No requirements extracted yet.' : 'Upload a sow_ prefixed document, then interpret it.'}
                </div>
                <button className="btn btn-purple" disabled={interpreting || !docs.some(d => d.role === 'sow')} onClick={handleInterpret}>
                  {interpreting ? 'Interpreting…' : '⟳ Interpret requirements'}
                </button>
              </div>
            ) : requirements.map(req => (
              <div key={req.id} style={{ padding: '14px 16px', borderBottom: '1px solid #1b2536', display: 'flex', gap: 12 }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 4 }}>{req.title}</div>
                  <div style={{ color: '#8a97ac', fontSize: 12, marginBottom: 6 }}>"{req.extracted}"</div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    {req.source && (
                      <span className="mono" style={{ fontSize: 11, background: 'rgba(136,146,164,0.1)', color: '#8a97ac', border: '1px solid rgba(136,146,164,0.2)', borderRadius: 5, padding: '2px 7px' }}>
                        {req.source}
                      </span>
                    )}
                    {req.confidence != null && (
                      <span style={{ fontSize: 11, color: '#6b7990' }}>{Math.round(req.confidence * 100)}% extraction confidence</span>
                    )}
                  </div>
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6, flexShrink: 0 }}>
                  {req.validation === 'pending' ? (
                    <>
                      <button className="btn btn-green" style={{ fontSize: 11, padding: '4px 10px' }} onClick={() => toggle(req.id, req.validation, 'validated')}>✓ Validate</button>
                      <button className="btn btn-amber" style={{ fontSize: 11, padding: '4px 10px' }} onClick={() => toggle(req.id, req.validation, 'flagged')}>⚑ Flag</button>
                    </>
                  ) : req.validation === 'validated' ? (
                    <>
                      <span style={{ background: 'rgba(52,211,153,0.15)', color: '#34d399', border: '1px solid rgba(52,211,153,0.35)', borderRadius: 6, fontSize: 11, padding: '4px 10px', fontWeight: 600 }}>✓ Validated</span>
                      <button className="btn" style={{ fontSize: 11, padding: '3px 8px', background: 'transparent', border: '1px solid #1f2b40', color: '#8a97ac' }} onClick={() => toggle(req.id, req.validation, 'validated')}>Undo</button>
                    </>
                  ) : (
                    <>
                      <span style={{ background: 'rgba(227,179,65,0.15)', color: '#e3b341', border: '1px solid rgba(227,179,65,0.35)', borderRadius: 6, fontSize: 11, padding: '4px 10px', fontWeight: 600 }}>⚑ Flagged</span>
                      <button className="btn" style={{ fontSize: 11, padding: '3px 8px', background: 'transparent', border: '1px solid #1f2b40', color: '#8a97ac' }} onClick={() => toggle(req.id, req.validation, 'flagged')}>Undo</button>
                    </>
                  )}
                </div>
              </div>
            ))}
          </div>

          {/* Gate footer */}
          {total > 0 && (
            <div style={{ padding: '16px', borderTop: '1px solid #1f2b40', background: '#121a28' }}>
              {allValidated ? (
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                  <span style={{ color: '#34d399', fontSize: 13, fontWeight: 500 }}>✅ All requirements validated</span>
                  <button className="btn btn-purple-solid" style={{ fontWeight: 600 }} onClick={handleGenerate}>
                    ✦ Proceed to draft generation →
                  </button>
                </div>
              ) : (
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                  <span style={{ color: '#6b7990', fontSize: 12 }}>Validate every requirement to unlock… ({validatedCount}/{total})</span>
                  <button className="btn" style={{ background: '#1f2b40', color: '#6b7990', border: 'none', cursor: 'not-allowed', fontWeight: 600 }} disabled>
                    ✦ Proceed to draft generation
                  </button>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
