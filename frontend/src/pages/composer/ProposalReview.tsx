import { useState } from 'react';
import { useNavigate } from '../../router';

// API not yet built — static preview data

type GapStatus = 'completed' | 'linked' | 'complete';

interface Req {
  id: string;
  num: string;
  title: string;
  gapStatus: GapStatus;
  sim: number;
  sowText: string;
  response?: string;
  gapNote?: string;
  linkDoc?: string;
  citations?: { doc: string; score: number }[];
}

const REQS: Req[] = [
  {
    id: 'r4', num: '3.6', title: 'Delivery within 60 days of award',
    gapStatus: 'completed', sim: 0.08,
    sowText: 'The supplier shall deliver all lots within 60 days of contract award date to the designated delivery points as specified in Annex B.',
    gapNote: 'No supporting documentation found. A delivery-schedule or logistics commitment document must be added before submission.',
  },
  {
    id: 'r5', num: '3.8', title: 'Spare-parts availability — 10 years',
    gapStatus: 'completed', sim: 0.11,
    sowText: 'The manufacturer shall guarantee availability of all spare parts and consumables for a minimum period of 10 years from the date of delivery.',
    gapNote: 'No supporting documentation found. A spare-parts commitment letter from the manufacturer must be added.',
  },
  {
    id: 'r6', num: '4.1', title: 'ISO 5912 conformity',
    gapStatus: 'linked', sim: 0.29,
    sowText: 'All tents supplied under this contract must conform to ISO 5912:2011 — Mountaineering and camping equipment — Tents — Requirements and test methods.',
    response: 'Our tent range has been independently tested and certified to ISO 5912:2011 standards. The certification was conducted by Bureau Veritas (certificate ref. BV-2024-05912) and covers all models included in this tender lot.',
    linkDoc: 'tech_ISO-5912-cert.pdf',
    citations: [{ doc: 'tech_ISO-5912-conformity-cert.pdf', score: 0.29 }],
  },
  {
    id: 'r7', num: '3.9', title: 'Wind load resistance ≥ 100 km/h',
    gapStatus: 'linked', sim: 0.24,
    sowText: 'Structure and fabric shall withstand sustained wind speeds of at least 100 km/h without deformation or failure, tested per EN 13782.',
    response: 'Our modular shelter frames are engineered to EN 13782 wind load standards. The aluminium pole system (6061-T6 alloy, tensile strength 276 MPa) has been independently load-tested at 115 km/h sustained.',
    linkDoc: 'Aluminium pole tensile strength cert.pdf',
    citations: [{ doc: 'Aluminium pole tensile strength — cert.pdf', score: 0.24 }],
  },
  {
    id: 'r8', num: '5.2', title: 'Packaging — individual weatherproof',
    gapStatus: 'linked', sim: 0.22,
    sowText: 'Each tent unit shall be packed in an individual weatherproof carry bag with printed identification label. Packaging shall protect against moisture and abrasion during transport.',
    response: 'Each unit is delivered in a 600D polyester carry bag with PVC base reinforcement and waterproof zipper. All bags are labelled with contract reference, lot number, and unit serial.',
    linkDoc: 'tech_fabric-spec-600D-polyester.pdf',
    citations: [{ doc: 'Tent fabric spec — 600D polyester.pdf', score: 0.22 }],
  },
  {
    id: 'r1', num: '4.2', title: 'Fire resistance — class M2 or better',
    gapStatus: 'complete', sim: 0.61,
    sowText: 'All fabric components, including flysheet, inner tent, and groundsheet, shall achieve a minimum fire resistance classification of M2 per NF P92-507.',
    response: 'All fabric components supplied under this contract meet M2 fire resistance classification per NF P92-507, as certified by SGS France (certificate ref. SGS-FR-2025-M2-0047, valid until 31 December 2026). The certification covers the 600D PES flysheet, inner tent fabric, and groundsheet.',
    citations: [
      { doc: 'SGS fire resistance test report — M2.pdf', score: 0.61 },
      { doc: 'Tent fabric spec — 600D polyester.pdf', score: 0.44 },
    ],
  },
  {
    id: 'r2', num: '3.1', title: 'Water column ≥ 2000 mm',
    gapStatus: 'complete', sim: 0.58,
    sowText: 'The flysheet fabric shall achieve a minimum hydrostatic head of 2000 mm per ISO 811 (Textiles — Determination of resistance to water penetration — Hydrostatic pressure test).',
    response: 'Our 600D PES flysheet fabric achieves a hydrostatic head of 3000 mm per ISO 811, exceeding the 2000 mm minimum requirement. This specification is confirmed in the fabric datasheet (ref. FAB-2024-600D).',
    citations: [{ doc: 'Tent fabric spec — 600D polyester.pdf', score: 0.58 }],
  },
  {
    id: 'r3', num: '3.4', title: 'UV resistance — min 1000h',
    gapStatus: 'complete', sim: 0.52,
    sowText: 'Fabric materials shall withstand a minimum of 1000 hours of UV exposure without significant degradation of mechanical properties, tested per ISO 4892-2.',
    response: 'The 600D PES fabric is treated with UV-stabilised PU coating rated for 1000+ hours per ISO 4892-2. Tensile strength retention after UV exposure is ≥ 85% of initial values.',
    citations: [{ doc: 'Tent fabric spec — 600D polyester.pdf', score: 0.52 }],
  },
  {
    id: 'r9', num: '4.5', title: 'Fabric weight ≤ 450 g/m²',
    gapStatus: 'complete', sim: 0.47,
    sowText: 'The flysheet fabric weight shall not exceed 450 g/m² to ensure portability within the specified pack weight limits.',
    response: 'Our 600D PES flysheet fabric weighs 320 g/m², well within the 450 g/m² limit. This provides adequate strength while contributing to the target pack weight of under 8 kg per unit.',
    citations: [{ doc: 'Tent fabric spec — 600D polyester.pdf', score: 0.47 }],
  },
];

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

type Filter = 'all' | GapStatus;

export default function ProposalReview() {
  const navigate = useNavigate();
  const [selectedId, setSelectedId] = useState('r4');
  const [filter, setFilter] = useState<Filter>('all');
  const [refineText, setRefineText] = useState('');

  const counts = {
    completed: REQS.filter(r => r.gapStatus === 'completed').length,
    linked: REQS.filter(r => r.gapStatus === 'linked').length,
    complete: REQS.filter(r => r.gapStatus === 'complete').length,
  };
  const filtered = filter === 'all' ? REQS : REQS.filter(r => r.gapStatus === filter);
  const selected = REQS.find(r => r.id === selectedId) ?? REQS[0];

  const filters: { key: Filter; label: string; count: number }[] = [
    { key: 'all', label: `All · ${REQS.length}`, count: REQS.length },
    { key: 'completed', label: `To complete · ${counts.completed}`, count: counts.completed },
    { key: 'linked', label: `To link · ${counts.linked}`, count: counts.linked },
    { key: 'complete', label: `Complete · ${counts.complete}`, count: counts.complete },
  ];

  return (
    <div>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 6, flexWrap: 'wrap' }}>
        <h1 style={{ fontSize: 28, fontWeight: 700 }}>Proposal Review</h1>
        <span style={{ background: 'rgba(192,132,252,0.15)', color: '#c084fc', border: '1px solid rgba(192,132,252,0.3)', borderRadius: 6, fontSize: 11, fontWeight: 700, padding: '3px 8px' }}>COMPOSER</span>
        <span style={{ background: 'rgba(227,179,65,0.12)', color: '#e3b341', border: '1px solid rgba(227,179,65,0.3)', borderRadius: 6, fontSize: 11, fontWeight: 700, padding: '3px 8px' }}>🚧 UNDER CONSTRUCTION</span>
      </div>
      <p style={{ color: '#8a97ac', marginBottom: 16, fontSize: 13 }}>
        Every response traces to the SOW. Review section by section, refine the prose, and clear the gaps before submission.
      </p>

      {/* Readiness banner */}
      <div style={{ background: 'rgba(227,179,65,0.07)', border: '1px solid rgba(227,179,65,0.3)', borderRadius: 10, padding: '16px 20px', marginBottom: 20, display: 'flex', alignItems: 'center', gap: 24, flexWrap: 'wrap' }}>
        <div style={{ flex: 1, minWidth: 200 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
            <span style={{ width: 8, height: 8, borderRadius: '50%', background: '#e3b341', display: 'inline-block', flexShrink: 0 }} />
            <span style={{ color: '#e3b341', fontWeight: 800, fontSize: 14 }}>NOT SUBMISSION-READY</span>
          </div>
          <div style={{ color: '#8a97ac', fontSize: 12 }}>
            Drafting <strong style={{ color: '#cdd6e3' }}>Fourniture de tentes de camping militaires</strong> · BOAMP · deadline 15 Jul 2026
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
          <button className="btn btn-amber" onClick={() => navigate('/composer/gaps')}>View gaps →</button>
          <button className="btn btn-ghost">⤓ .docx</button>
          <button className="btn btn-ghost">⤓ matrix .xlsx</button>
        </div>
      </div>

      {/* Master / detail */}
      <div style={{ display: 'grid', gridTemplateColumns: '340px 1fr', gap: 20 }}>
        {/* Left — list */}
        <div className="card" style={{ padding: 0, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
          {/* Filter chips */}
          <div style={{ padding: '12px 14px', borderBottom: '1px solid #1f2b40', display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {filters.map(f => (
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
              const color = STATUS_COLORS[req.gapStatus];
              return (
                <div
                  key={req.id}
                  onClick={() => setSelectedId(req.id)}
                  style={{
                    padding: '12px 14px', borderBottom: '1px solid #1b2536', cursor: 'pointer',
                    borderLeft: isActive ? `3px solid #c084fc` : '3px solid transparent',
                    background: isActive ? 'rgba(192,132,252,0.06)' : 'transparent',
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                    <span style={{ width: 8, height: 8, borderRadius: '50%', background: color, display: 'inline-block', flexShrink: 0 }} />
                    <span className="mono" style={{ fontSize: 11, color: '#6b7990', flexShrink: 0 }}>{req.num}</span>
                    <span style={{ fontSize: 13, fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{req.title}</span>
                  </div>
                  <div style={{ marginLeft: 16, display: 'flex', gap: 8, alignItems: 'center' }}>
                    <span style={{ fontSize: 11, color }}>{STATUS_LABELS[req.gapStatus]}</span>
                    <span className="mono" style={{ fontSize: 11, color: '#6b7990' }}>sim {req.sim.toFixed(2)}</span>
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {/* Right — detail */}
        {selected && (
          <div className="card" style={{ padding: '20px 22px', display: 'flex', flexDirection: 'column', gap: 18, alignSelf: 'start' }}>
            {/* Header */}
            <div style={{ display: 'flex', alignItems: 'flex-start', gap: 16 }}>
              <div style={{ flex: 1, minWidth: 200 }}>
                <div className="mono" style={{ fontSize: 11, color: '#6b7990', marginBottom: 6 }}>Requirement {selected.num}</div>
                <div style={{ fontSize: 20, fontWeight: 700, lineHeight: 1.3 }}>{selected.title}</div>
              </div>
              <div style={{ flexShrink: 0, textAlign: 'right' }}>
                <div style={{
                  background: STATUS_COLORS[selected.gapStatus] + '1a',
                  border: `1px solid ${STATUS_COLORS[selected.gapStatus]}44`,
                  color: STATUS_COLORS[selected.gapStatus],
                  borderRadius: 7, padding: '4px 10px', fontSize: 12, fontWeight: 600, marginBottom: 6, display: 'inline-block',
                }}>
                  {STATUS_LABELS[selected.gapStatus]}
                </div>
                <div className="mono" style={{ fontSize: 28, fontWeight: 700, color: STATUS_COLORS[selected.gapStatus], lineHeight: 1 }}>{selected.sim.toFixed(2)}</div>
                <div style={{ fontSize: 10, color: '#6b7990', textTransform: 'uppercase', letterSpacing: '0.08em', marginTop: 4 }}>SIMILARITY</div>
              </div>
            </div>

            {/* SOW text */}
            <div>
              <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.08em', color: '#6b7990', textTransform: 'uppercase', marginBottom: 8 }}>Requirement · from SOW</div>
              <div style={{ borderLeft: '3px solid rgba(136,146,164,0.3)', background: 'rgba(136,146,164,0.08)', padding: '10px 14px', borderRadius: '0 6px 6px 0', fontSize: 13, color: '#cdd6e3', lineHeight: 1.6 }}>
                {selected.sowText}
              </div>
            </div>

            {/* Generated response */}
            <div>
              <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.08em', color: '#6b7990', textTransform: 'uppercase', marginBottom: 8 }}>Generated response</div>
              {selected.gapStatus === 'completed' ? (
                <div style={{ border: '1.5px dashed rgba(248,113,113,0.4)', background: 'rgba(248,113,113,0.06)', padding: 14, borderRadius: 8 }}>
                  <div style={{ color: '#f87171', fontWeight: 700, marginBottom: 6 }}>⛔ To be completed</div>
                  <div style={{ color: '#8a97ac', fontSize: 13 }}>{selected.gapNote}</div>
                </div>
              ) : (
                <>
                  <div style={{ fontSize: 13, color: '#cdd6e3', lineHeight: 1.6, marginBottom: selected.linkDoc ? 10 : 0 }}>{selected.response}</div>
                  {selected.linkDoc && (
                    <div style={{ background: 'rgba(96,165,250,0.08)', border: '1px solid rgba(96,165,250,0.25)', borderRadius: 8, padding: '10px 14px', fontSize: 12, color: '#60a5fa', marginTop: 10 }}>
                      🔗 To be linked — <span className="mono">{selected.linkDoc}</span> awaiting formal linking
                    </div>
                  )}
                </>
              )}
            </div>

            {/* Citations */}
            {selected.citations && selected.citations.length > 0 && (
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

            {/* Refine */}
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
                <button className="btn btn-purple-solid" style={{ fontWeight: 600 }}>⟳ Regenerate section</button>
                <span style={{ color: '#6b7990', fontSize: 12 }}>v3 · last regenerated 27 Jun</span>
                <span style={{ color: '#8a97ac', fontSize: 12, cursor: 'pointer' }}>⌄ Version history</span>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
