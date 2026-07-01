import { useState } from 'react';
import type { VaultDoc } from '../../types';

// API not yet built — static preview data
const PREVIEW_DOCS: VaultDoc[] = [
  {
    id: '1',
    filename: 'Tent fabric spec — 600D polyester.pdf',
    doc_type: 'Datasheet',
    status: 'indexed',
    metadata: {
      'Material': '600D PES',
      'Water column': '3000 mm',
      'Fire rating': 'M2',
      'Weight': '320 g/m²',
      'UV resistance': '1000h',
      'Language': 'French',
    },
    cpv_codes: ['39522530', '39522500'],
    confidence: 0.94,
    fields_extracted: 6,
  },
  {
    id: '2',
    filename: 'Modular shelter frame — assembly drawing.dwg',
    doc_type: 'Drawing',
    status: 'indexed',
    metadata: {
      'Span': '6 m',
      'Load capacity': '120 kg/m²',
      'Standard': 'S235',
      'Material': 'Galvanised steel',
      'Language': 'English',
    },
    cpv_codes: ['44211000'],
    confidence: 0.87,
    fields_extracted: 5,
  },
  {
    id: '3',
    filename: 'ISO 5912 conformity certificate.pdf',
    doc_type: 'Certificate',
    status: 'indexed',
    metadata: {
      'Standard': 'ISO 5912:2011',
      'Issuer': 'Bureau Veritas',
      'Valid until': '2027-09-30',
      'Scope': 'Mountaineering tents',
      'Language': 'English/French',
    },
    cpv_codes: ['39522530'],
    confidence: 0.98,
    fields_extracted: 5,
  },
  {
    id: '4',
    filename: 'SGS fire resistance test report — M2.pdf',
    doc_type: 'Certificate',
    status: 'indexed',
    metadata: {
      'Fire rating': 'M2',
      'Test method': 'NF P92-507',
      'Issuer': 'SGS France',
      'Valid until': '2026-12-31',
      'Language': 'French',
    },
    cpv_codes: ['39522530', '39522500'],
    confidence: 0.96,
    fields_extracted: 5,
  },
  {
    id: '5',
    filename: 'Waterproof membrane spec — TPU coating.pdf',
    doc_type: 'Datasheet',
    status: 'processing',
    metadata: {},
    cpv_codes: [],
    confidence: 0,
    fields_extracted: 0,
  },
  {
    id: '6',
    filename: 'Aluminium pole tensile strength — cert.pdf',
    doc_type: 'Certificate',
    status: 'indexed',
    metadata: {
      'Alloy': '6061-T6',
      'Tensile strength': '276 MPa',
      'Diameter': '16–32 mm',
      'Standard': 'EN 573',
      'Language': 'German',
    },
    cpv_codes: ['44211000', '44212310'],
    confidence: 0.91,
    fields_extracted: 5,
  },
];

function TypePill({ type }: { type: string }) {
  return (
    <span style={{
      background: 'rgba(136,146,164,0.12)', color: '#8a97ac',
      border: '1px solid rgba(136,146,164,0.2)', borderRadius: 5,
      fontSize: 11, fontWeight: 600, padding: '2px 7px', whiteSpace: 'nowrap',
    }}>
      {type}
    </span>
  );
}

function MetadataChip({ label, value }: { label: string; value: string }) {
  return (
    <span className="mono" style={{
      background: 'rgba(96,165,250,0.08)', color: '#9cc1fb',
      border: '1px solid rgba(96,165,250,0.18)', borderRadius: 5,
      fontSize: 11, padding: '2px 7px', whiteSpace: 'nowrap',
    }}>
      {label} {value}
    </span>
  );
}

export default function VaultLibrary() {
  const [selectedId, setSelectedId] = useState<string | null>('1');
  const [search, setSearch] = useState('');

  const filtered = PREVIEW_DOCS.filter(d =>
    !search || d.filename.toLowerCase().includes(search.toLowerCase())
  );
  const selected = PREVIEW_DOCS.find(d => d.id === selectedId) ?? null;

  const total = PREVIEW_DOCS.length;
  const processing = PREVIEW_DOCS.filter(d => d.status === 'processing').length;

  return (
    <div>
      {/* Page header */}
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 6, flexWrap: 'wrap' }}>
        <h1 style={{ fontSize: 28, fontWeight: 700 }}>Library</h1>
        <span style={{ background: 'rgba(96,165,250,0.15)', color: '#60a5fa', border: '1px solid rgba(96,165,250,0.3)', borderRadius: 6, fontSize: 11, fontWeight: 700, padding: '3px 8px', letterSpacing: '0.06em' }}>
          VAULT
        </span>
        <span style={{ background: 'rgba(227,179,65,0.12)', color: '#e3b341', border: '1px solid rgba(227,179,65,0.3)', borderRadius: 6, fontSize: 11, fontWeight: 700, padding: '3px 8px' }}>
          🚧 UNDER CONSTRUCTION
        </span>
      </div>
      <p style={{ color: '#8a97ac', marginBottom: 20, fontSize: 13 }}>
        Ingest a library of technical documents — Vault extracts structured metadata that Scout and Composer reuse
      </p>

      {/* Under-construction banner */}
      <div style={{ background: 'rgba(227,179,65,0.07)', border: '1px solid rgba(227,179,65,0.25)', borderRadius: 10, padding: '12px 16px', marginBottom: 20, display: 'flex', alignItems: 'center', gap: 10 }} className="under-construction-stripe">
        <span style={{ color: '#e3b341', fontSize: 18 }}>⚠</span>
        <div>
          <span style={{ fontWeight: 600, color: '#e3b341', fontSize: 13 }}>In active development</span>
          <span style={{ color: '#8a97ac', fontSize: 13 }}> · Preview of the Vault app — interface is not yet functional</span>
        </div>
      </div>

      {/* Search + action */}
      <div style={{ display: 'flex', gap: 12, marginBottom: 16, alignItems: 'center' }}>
        <div style={{ position: 'relative', flex: '0 0 360px' }}>
          <span style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', color: '#4c5a70', fontSize: 14 }}>⌕</span>
          <input
            className="input-field"
            style={{ paddingLeft: 30 }}
            placeholder="Search specs, datasheets, certificates…"
            value={search}
            onChange={e => setSearch(e.target.value)}
          />
        </div>
        <button className="btn btn-blue-solid" style={{ marginLeft: 'auto', whiteSpace: 'nowrap' }}>
          ⤓ Ingest documents
        </button>
      </div>

      {/* 2-column grid */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
        {/* Left — indexed documents */}
        <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '14px 16px', borderBottom: '1px solid #1f2b40' }}>
            <span style={{ fontWeight: 600, fontSize: 13 }}>Indexed documents</span>
            <span style={{ color: '#8a97ac', fontSize: 12 }}>
              {total} total{processing > 0 ? ` · ${processing} processing` : ''}
            </span>
          </div>
          {filtered.map(doc => {
            const isSelected = doc.id === selectedId;
            return (
              <div
                key={doc.id}
                onClick={() => setSelectedId(doc.id)}
                style={{
                  padding: '12px 16px',
                  borderBottom: '1px solid #1b2536',
                  cursor: 'pointer',
                  borderLeft: isSelected ? '3px solid #60a5fa' : '3px solid transparent',
                  background: isSelected ? 'rgba(96,165,250,0.06)' : 'transparent',
                  transition: 'background 0.1s',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                  <span className="mono" style={{ fontSize: 12, color: '#cdd6e3', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {doc.filename}
                  </span>
                  <TypePill type={doc.doc_type} />
                </div>
                {Object.keys(doc.metadata).length > 0 && (
                  <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap', marginBottom: 6 }}>
                    {Object.entries(doc.metadata).slice(0, 3).map(([k, v]) => (
                      <MetadataChip key={k} label={k === 'Water column' ? '' : ''} value={`${k === 'Water column' ? v : k === 'Material' ? v : v}`} />
                    ))}
                  </div>
                )}
                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  {doc.status === 'indexed' ? (
                    <><span className="dot-live" /><span style={{ fontSize: 11, color: '#34d399' }}>Indexed</span></>
                  ) : (
                    <><span style={{ fontSize: 11 }}>⏳</span><span style={{ fontSize: 11, color: '#e3b341' }}>Processing</span></>
                  )}
                </div>
              </div>
            );
          })}
        </div>

        {/* Right — extracted metadata */}
        <div className="card" style={{ padding: 0, overflow: 'hidden', alignSelf: 'start', position: 'sticky', top: 0 }}>
          {!selected ? (
            <div style={{ padding: 40, textAlign: 'center', color: '#8a97ac', fontSize: 13 }}>
              Select a document to view extracted metadata
            </div>
          ) : (
            <>
              <div style={{ padding: '14px 16px', borderBottom: '1px solid #1f2b40' }}>
                <span className="mono" style={{ fontSize: 12, color: '#cdd6e3', display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {selected.filename}
                </span>
              </div>
              {selected.status === 'processing' ? (
                <div style={{ padding: 24, color: '#e3b341', fontSize: 13 }}>
                  ⏳ Extracting fields — not yet available for retrieval
                </div>
              ) : (
                <div style={{ padding: '14px 16px' }}>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px 20px', marginBottom: 16 }}>
                    {Object.entries(selected.metadata).map(([label, value]) => (
                      <div key={label}>
                        <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.08em', color: '#6b7990', textTransform: 'uppercase', marginBottom: 3 }}>
                          {label}
                        </div>
                        <div className="mono" style={{ fontSize: 13, color: '#cdd6e3' }}>{value}</div>
                      </div>
                    ))}
                  </div>
                  {selected.cpv_codes.length > 0 && (
                    <div style={{ borderTop: '1px solid #1f2b40', paddingTop: 12, marginBottom: 12 }}>
                      <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.08em', color: '#6b7990', textTransform: 'uppercase', marginBottom: 6 }}>
                        Linked CPV codes
                      </div>
                      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                        {selected.cpv_codes.map(code => (
                          <span key={code} className="mono" style={{ background: 'rgba(46,230,212,0.1)', color: '#2EE6D4', border: '1px solid rgba(46,230,212,0.25)', borderRadius: 5, fontSize: 11, padding: '2px 8px' }}>
                            {code}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}
                  <div style={{ borderTop: '1px solid #1f2b40', paddingTop: 10, display: 'flex', gap: 12, color: '#8a97ac', fontSize: 12 }}>
                    <span>{selected.fields_extracted} fields extracted</span>
                    <span>·</span>
                    <span>{Math.round(selected.confidence * 100)}% confidence</span>
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
