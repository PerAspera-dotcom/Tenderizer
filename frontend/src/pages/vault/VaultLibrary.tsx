import { useEffect, useRef, useState } from 'react';
import { listVaultDocs, uploadVaultDoc } from '../../api';
import type { VaultDoc } from '../../types';

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

function MetadataChip({ value }: { value: string }) {
  return (
    <span className="mono" style={{
      background: 'rgba(96,165,250,0.08)', color: '#9cc1fb',
      border: '1px solid rgba(96,165,250,0.18)', borderRadius: 5,
      fontSize: 11, padding: '2px 7px', whiteSpace: 'nowrap',
    }}>
      {value}
    </span>
  );
}

export default function VaultLibrary() {
  const [docs, setDocs] = useState<VaultDoc[]>([]);
  const [total, setTotal] = useState(0);
  const [processing, setProcessing] = useState(0);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [search, setSearch] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [uploading, setUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  async function refresh(q: string) {
    try {
      const body = await listVaultDocs(q || undefined);
      setDocs(body.results);
      setTotal(body.total);
      setProcessing(body.processing);
      setError('');
    } catch {
      setError('Failed to load Vault documents');
    } finally {
      setLoading(false);
    }
  }

  // Debounced search — a server-side query, not a client-side filter, since
  // metadata search needs to happen where the data lives.
  useEffect(() => {
    const handle = setTimeout(() => refresh(search), 250);
    return () => clearTimeout(handle);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search]);

  // Poll while anything is still processing (parse/embed/metadata-extraction
  // run as a background task — see src/vault.py's process_upload).
  useEffect(() => {
    if (processing === 0) return;
    const handle = setInterval(() => refresh(search), 3000);
    return () => clearInterval(handle);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [processing, search]);

  const selected = docs.find(d => d.id === selectedId) ?? null;

  async function handleFilesSelected(files: FileList | null) {
    if (!files || files.length === 0) return;
    setUploading(true);
    try {
      for (const file of Array.from(files)) {
        await uploadVaultDoc(file);
      }
      await refresh(search);
    } catch {
      setError('Upload failed — try again');
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  }

  if (loading) return <div className="loading">Loading…</div>;

  return (
    <div>
      {/* Page header */}
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 6, flexWrap: 'wrap' }}>
        <h1 style={{ fontSize: 28, fontWeight: 700 }}>Library</h1>
        <span style={{ background: 'rgba(96,165,250,0.15)', color: '#60a5fa', border: '1px solid rgba(96,165,250,0.3)', borderRadius: 6, fontSize: 11, fontWeight: 700, padding: '3px 8px', letterSpacing: '0.06em' }}>
          VAULT
        </span>
      </div>
      <p style={{ color: '#8a97ac', marginBottom: 20, fontSize: 13 }}>
        Ingest a library of technical documents — Vault extracts structured metadata that Scout and Composer reuse
      </p>

      {error && <div style={{ color: '#f87171', fontSize: 13, marginBottom: 12 }}>{error}</div>}

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
        <input
          ref={fileInputRef}
          type="file"
          accept=".pdf,.docx"
          multiple
          style={{ display: 'none' }}
          onChange={e => handleFilesSelected(e.target.files)}
        />
        <button
          className="btn btn-blue-solid"
          style={{ marginLeft: 'auto', whiteSpace: 'nowrap' }}
          disabled={uploading}
          onClick={() => fileInputRef.current?.click()}
        >
          {uploading ? 'Uploading…' : '⤓ Ingest documents'}
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
          {docs.length === 0 ? (
            <div style={{ padding: 32, textAlign: 'center', color: '#8a97ac', fontSize: 13 }}>
              {search ? 'No documents match your search.' : 'No documents yet — ingest one to get started.'}
            </div>
          ) : docs.map(doc => {
            const isSelected = doc.id === selectedId;
            const metadataEntries = Object.entries(doc.metadata);
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
                  {doc.doc_type && <TypePill type={doc.doc_type} />}
                </div>
                {metadataEntries.length > 0 && (
                  <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap', marginBottom: 6 }}>
                    {metadataEntries.slice(0, 3).map(([k, v]) => (
                      <MetadataChip key={k} value={`${k}: ${v}`} />
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
                  {Object.keys(selected.metadata).length === 0 ? (
                    <div style={{ color: '#8a97ac', fontSize: 13, marginBottom: 16 }}>
                      No technical fields were extracted for this document.
                    </div>
                  ) : (
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
                  )}
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
                  {selected.fields_extracted !== null && selected.confidence !== null && (
                    <div style={{ borderTop: '1px solid #1f2b40', paddingTop: 10, display: 'flex', gap: 12, color: '#8a97ac', fontSize: 12 }}>
                      <span>{selected.fields_extracted} fields extracted</span>
                      <span>·</span>
                      <span>{Math.round(selected.confidence * 100)}% confidence</span>
                    </div>
                  )}
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
