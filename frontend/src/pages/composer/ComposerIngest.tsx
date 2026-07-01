import { useState } from 'react';
import { useNavigate } from '../../router';
import { patchRequirement } from '../../api';

// API not yet built — static preview data

const DOC_ROLES = {
  sow: { label: 'SOW', color: '#60a5fa' },
  tech: { label: 'TECH', color: '#e3b341' },
  background: { label: 'BG', color: '#2EE6D4' },
  parta: { label: 'PART A', color: '#c084fc' },
  example: { label: 'EXAMPLE', color: '#8a97ac' },
} as const;

type DocRole = keyof typeof DOC_ROLES;

interface PreviewDoc {
  id: string;
  filename: string;
  role: DocRole;
  pages: number;
  chunks: number;
  status: 'ingested' | 'pending' | 'style_only';
  image_heavy?: boolean;
}

const PREVIEW_DOCS: PreviewDoc[] = [
  { id: 'd1', filename: 'sow_appel-offres-tentes-militaires.pdf', role: 'sow', pages: 24, chunks: 186, status: 'ingested' },
  { id: 'd2', filename: 'tech_fabric-spec-600D-polyester.pdf', role: 'tech', pages: 8, chunks: 64, status: 'ingested', image_heavy: true },
  { id: 'd3', filename: 'tech_ISO-5912-conformity-cert.pdf', role: 'tech', pages: 3, chunks: 22, status: 'ingested' },
  { id: 'd4', filename: 'background_company-profile-2026.pdf', role: 'background', pages: 12, chunks: 91, status: 'ingested' },
  { id: 'd5', filename: 'parta_qualification-form.pdf', role: 'parta', pages: 6, chunks: 44, status: 'pending' },
  { id: 'd6', filename: 'example_proposal-2024-DGA.pdf', role: 'example', pages: 38, chunks: 0, status: 'style_only' },
  { id: 'd7', filename: 'compliance_matrix_template.xlsx', role: 'tech', pages: 1, chunks: 0, status: 'pending' },
];

interface Req {
  id: string;
  num: string;
  title: string;
  extracted: string;
  source: string;
  confidence: number;
}

const PREVIEW_REQS: Req[] = [
  { id: 'r1', num: '4.2', title: 'Fire resistance — class M2 or better', extracted: 'M2 / NF P92-507 required on all fabric components', source: 'CCTP §4.2 · p.12', confidence: 96 },
  { id: 'r2', num: '3.1', title: 'Water column ≥ 2000 mm', extracted: 'Waterproofing: minimum 2000 mm water column per ISO 811', source: 'CCTP §3.1 · p.8', confidence: 91 },
  { id: 'r3', num: '3.4', title: 'UV resistance — min 1000h', extracted: 'Fabric shall withstand 1000h UV exposure without degradation', source: 'CCTP §3.4 · p.10', confidence: 88 },
  { id: 'r4', num: '7.1', title: 'Delivery within 60 days of award', extracted: 'The supplier shall deliver all lots within 60 days of contract award', source: 'CCTP §7.1 · p.23', confidence: 84 },
  { id: 'r5', num: '8.2', title: 'Spare-parts availability — 10 years', extracted: 'Manufacturer guarantees spare parts availability for at least 10 years', source: 'CCTP §8.2 · p.27', confidence: 79 },
  { id: 'r6', num: '4.1', title: 'ISO 5912 conformity', extracted: 'All tents must conform to ISO 5912:2011 for mountaineering equipment', source: 'CCTP §4.1 · p.11', confidence: 93 },
];

function RoleTag({ role }: { role: DocRole }) {
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

function Stepper() {
  const steps = [
    { num: 1, label: 'Ingest', state: 'done' },
    { num: 2, label: 'Interpret', state: 'done' },
    { num: 3, label: 'Validate', state: 'active' },
    { num: 4, label: 'Generate draft', state: 'locked' },
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
  const [validations, setValidations] = useState<Record<string, 'pending' | 'validated' | 'flagged'>>(() =>
    Object.fromEntries(PREVIEW_REQS.map(r => [r.id, 'pending']))
  );

  function toggle(id: string, next: 'validated' | 'flagged') {
    setValidations(v => ({ ...v, [id]: v[id] === next ? 'pending' : next }));
    patchRequirement(id, next).catch(() => {}); // best-effort, server may not exist yet
  }

  const validatedCount = Object.values(validations).filter(v => v === 'validated').length;
  const total = PREVIEW_REQS.length;
  const allValidated = validatedCount === total;

  return (
    <div>
      {/* Page header */}
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 6, flexWrap: 'wrap' }}>
        <h1 style={{ fontSize: 28, fontWeight: 700 }}>Ingest & Config</h1>
        <span style={{ background: 'rgba(192,132,252,0.15)', color: '#c084fc', border: '1px solid rgba(192,132,252,0.3)', borderRadius: 6, fontSize: 11, fontWeight: 700, padding: '3px 8px', letterSpacing: '0.06em' }}>
          COMPOSER
        </span>
        <span style={{ background: 'rgba(227,179,65,0.12)', color: '#e3b341', border: '1px solid rgba(227,179,65,0.3)', borderRadius: 6, fontSize: 11, fontWeight: 700, padding: '3px 8px' }}>
          🚧 UNDER CONSTRUCTION
        </span>
      </div>
      <p style={{ color: '#8a97ac', marginBottom: 20, fontSize: 13 }}>
        Drop in the tender documents — Composer reads and interprets them, then the responsible person validates each requirement before any draft is generated
      </p>

      <Stepper />

      {/* 2-column grid */}
      <div style={{ display: 'grid', gridTemplateColumns: '300px 1fr', gap: 24 }}>
        {/* Left column */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          {/* Drop zone */}
          <div style={{
            border: '1.5px dashed rgba(192,132,252,0.35)',
            background: 'rgba(192,132,252,0.04)',
            borderRadius: 10, padding: '28px 20px',
            textAlign: 'center',
          }}>
            <div style={{ fontSize: 28, color: '#c084fc', marginBottom: 10 }}>⤓</div>
            <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 8 }}>Drop tender documents</div>
            <div style={{ color: '#8a97ac', fontSize: 12, lineHeight: 1.6 }}>
              Role auto-detected from filename prefix —{' '}
              <span className="mono" style={{ color: '#cdd6e3', fontSize: 11 }}>sow_ · tech_ · background_ · parta_ · example_</span>
            </div>
          </div>

          {/* Document library */}
          <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '12px 14px', borderBottom: '1px solid #1f2b40' }}>
              <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.1em', color: '#6b7990', textTransform: 'uppercase' }}>Document library</span>
              <span style={{ color: '#8a97ac', fontSize: 12 }}>{PREVIEW_DOCS.length} files</span>
            </div>
            {PREVIEW_DOCS.map(doc => (
              <div key={doc.id} style={{ padding: '10px 14px', borderBottom: '1px solid #1b2536' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: doc.image_heavy ? 4 : 0 }}>
                  <RoleTag role={doc.role} />
                  <span className="mono" style={{ fontSize: 11, color: '#cdd6e3', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {doc.filename}
                  </span>
                </div>
                <div style={{ marginLeft: 48, fontSize: 11, color: '#8a97ac', marginBottom: doc.image_heavy ? 4 : 0 }}>
                  {doc.status !== 'style_only' ? `${doc.pages} pages · ${doc.chunks} chunks` : ''}
                  {' '}
                  {doc.status === 'ingested' && <><span className="dot-live" style={{ width: 6, height: 6, marginRight: 4 }} /><span style={{ color: '#34d399' }}>Ingested</span></>}
                  {doc.status === 'pending' && <><span className="dot-paused" style={{ width: 6, height: 6, marginRight: 4 }} /><span style={{ color: '#e3b341' }}>Pending</span></>}
                  {doc.status === 'style_only' && <><span style={{ color: '#8a97ac' }}>◌ Style only</span></>}
                </div>
                {doc.image_heavy && (
                  <div style={{ marginLeft: 48, marginTop: 4, fontSize: 11, color: '#e3b341', background: 'rgba(227,179,65,0.08)', border: '1px solid rgba(227,179,65,0.2)', borderRadius: 5, padding: '3px 8px' }}>
                    ⚠ Image-heavy PDF — run datasheet enrichment to extract specs
                  </div>
                )}
              </div>
            ))}
            <div style={{ padding: '10px 14px', display: 'flex', gap: 8 }}>
              <button className="btn btn-purple" style={{ fontSize: 12 }}>⟳ Ingest all</button>
              <button className="btn btn-amber" style={{ fontSize: 12 }}>✨ Enrich datasheets</button>
            </div>
          </div>

          {/* Compliance matrix */}
          <div className="card" style={{ padding: '12px 14px', display: 'flex', alignItems: 'center', gap: 10 }}>
            <span style={{ fontSize: 18, color: '#8a97ac' }}>▦</span>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div className="mono" style={{ fontSize: 12, color: '#cdd6e3', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>compliance_matrix.xlsx</div>
              <div style={{ fontSize: 11, color: '#8a97ac' }}>Compliance matrix · 42 requirements</div>
            </div>
            <span className="pill pill-green" style={{ fontSize: 11, whiteSpace: 'nowrap' }}>
              <span className="dot-live" style={{ width: 6, height: 6 }} /> Loaded
            </span>
          </div>
        </div>

        {/* Right column — Interpreted requirements */}
        <div className="card" style={{ padding: 0, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
          <div style={{ padding: '14px 16px', borderBottom: '1px solid #1f2b40' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
              <span style={{ fontWeight: 600, fontSize: 14 }}>Interpreted requirements</span>
              <span style={{ color: '#8a97ac', fontSize: 12 }}>{validatedCount} of {total} validated</span>
            </div>
            {/* Progress bar */}
            <div style={{ height: 4, background: '#1f2b40', borderRadius: 9999, overflow: 'hidden' }}>
              <div style={{ height: '100%', width: `${(validatedCount / total) * 100}%`, background: '#c084fc', borderRadius: 9999, transition: 'width 0.3s' }} />
            </div>
          </div>

          <div style={{ flex: 1, overflowY: 'auto' }}>
            {PREVIEW_REQS.map(req => {
              const state = validations[req.id];
              return (
                <div key={req.id} style={{ padding: '14px 16px', borderBottom: '1px solid #1b2536', display: 'flex', gap: 12 }}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 4 }}>{req.title}</div>
                    <div style={{ color: '#8a97ac', fontSize: 12, marginBottom: 6 }}>"{req.extracted}"</div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <span className="mono" style={{ fontSize: 11, background: 'rgba(136,146,164,0.1)', color: '#8a97ac', border: '1px solid rgba(136,146,164,0.2)', borderRadius: 5, padding: '2px 7px' }}>
                        {req.source}
                      </span>
                      <span style={{ fontSize: 11, color: '#6b7990' }}>{req.confidence}% extraction confidence</span>
                    </div>
                  </div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 6, flexShrink: 0 }}>
                    {state === 'pending' ? (
                      <>
                        <button className="btn btn-green" style={{ fontSize: 11, padding: '4px 10px' }} onClick={() => toggle(req.id, 'validated')}>✓ Validate</button>
                        <button className="btn btn-amber" style={{ fontSize: 11, padding: '4px 10px' }} onClick={() => toggle(req.id, 'flagged')}>⚑ Flag</button>
                      </>
                    ) : state === 'validated' ? (
                      <>
                        <span style={{ background: 'rgba(52,211,153,0.15)', color: '#34d399', border: '1px solid rgba(52,211,153,0.35)', borderRadius: 6, fontSize: 11, padding: '4px 10px', fontWeight: 600 }}>✓ Validated</span>
                        <button className="btn" style={{ fontSize: 11, padding: '3px 8px', background: 'transparent', border: '1px solid #1f2b40', color: '#8a97ac' }} onClick={() => toggle(req.id, 'validated')}>Undo</button>
                      </>
                    ) : (
                      <>
                        <span style={{ background: 'rgba(227,179,65,0.15)', color: '#e3b341', border: '1px solid rgba(227,179,65,0.35)', borderRadius: 6, fontSize: 11, padding: '4px 10px', fontWeight: 600 }}>⚑ Flagged</span>
                        <button className="btn" style={{ fontSize: 11, padding: '3px 8px', background: 'transparent', border: '1px solid #1f2b40', color: '#8a97ac' }} onClick={() => toggle(req.id, 'flagged')}>Undo</button>
                      </>
                    )}
                  </div>
                </div>
              );
            })}
          </div>

          {/* Gate footer */}
          <div style={{ padding: '16px', borderTop: '1px solid #1f2b40', background: '#121a28' }}>
            {allValidated ? (
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                <span style={{ color: '#34d399', fontSize: 13, fontWeight: 500 }}>✅ All requirements validated</span>
                <button
                  className="btn btn-purple-solid"
                  style={{ fontWeight: 600 }}
                  onClick={() => navigate('/composer/review')}
                >
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
        </div>
      </div>
    </div>
  );
}
