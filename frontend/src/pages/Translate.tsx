import { useEffect, useRef, useState } from 'react';
import {
  getTranslationQuota, uploadTranslatedDocument, listTranslatedDocuments, retryTranslatedDocument,
  deleteTranslatedDocument, saveTranslatedDocumentToVault, downloadTranslatedDocumentBlob,
  downloadAllTranslatedDocumentsBlob, getHealth,
} from '../api';
import type { TranslatedDocument, TranslationQuota } from '../types';
import { formatDate } from '../utils';

// CR-012 §2: the CR's own accepted-format list — kept in sync with
// translate_document.ACCEPTED_EXTENSIONS server-side; this is a client-side
// courtesy check only, the server re-validates regardless.
const ACCEPTED_EXTENSIONS = ['.pdf', '.docx', '.pptx', '.xlsx', '.txt', '.html', '.htm', '.xlf', '.xliff', '.srt'];
const MAX_SIZE_BYTES = 30 * 1024 * 1024; // DeepL's Pro-tier 30MB/document limit

const IN_PROGRESS = new Set(['queued', 'translating']);

const STATUS_STYLE: Record<string, { bg: string; fg: string; label: string }> = {
  queued: { bg: 'rgba(148,163,184,0.1)', fg: '#94a3b8', label: 'Queued' },
  translating: { bg: 'rgba(96,165,250,0.1)', fg: '#60a5fa', label: 'Translating…' },
  ready: { bg: 'rgba(52,211,153,0.1)', fg: '#34d399', label: 'Ready' },
  failed: { bg: 'rgba(248,113,113,0.1)', fg: '#f87171', label: 'Failed' },
  already_english: { bg: 'rgba(148,163,184,0.1)', fg: '#94a3b8', label: 'Already English' },
};

function StatusBadge({ status }: { status: string }) {
  const s = STATUS_STYLE[status] ?? STATUS_STYLE.queued;
  return (
    <span style={{ background: s.bg, color: s.fg, border: `1px solid ${s.fg}55`, borderRadius: 9999, padding: '2px 10px', fontSize: 11, fontWeight: 600 }}>
      ● {s.label}
    </span>
  );
}

function extOf(filename: string): string {
  const i = filename.lastIndexOf('.');
  return i === -1 ? '' : filename.slice(i).toLowerCase();
}

export default function Translate() {
  const [docs, setDocs] = useState<TranslatedDocument[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [statusFilter, setStatusFilter] = useState('');

  const [dragOver, setDragOver] = useState(false);
  const [queuedFiles, setQueuedFiles] = useState<{ file: File; rejection: string | null }[]>([]);
  const [uploading, setUploading] = useState(false);
  const [quota, setQuota] = useState<TranslationQuota | null>(null);
  const [deeplDown, setDeeplDown] = useState(false);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [busyIds, setBusyIds] = useState<Set<number>>(new Set());
  const fileInputRef = useRef<HTMLInputElement>(null);

  function load() {
    listTranslatedDocuments({ status: statusFilter || undefined })
      .then(setDocs).catch(() => setError('Failed to load translated documents')).finally(() => setLoading(false));
  }

  useEffect(() => { load(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [statusFilter]);

  useEffect(() => {
    getHealth().then(h => {
      const deepl = h.find(x => x.name === 'DeepL');
      setDeeplDown(!!deepl && (deepl.status === 'failing' || deepl.status === 'unavailable'));
    }).catch(() => {});
  }, []);

  // Poll while anything is still queued/translating.
  useEffect(() => {
    if (!docs.some(d => IN_PROGRESS.has(d.status))) return;
    const t = setTimeout(load, 4000);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [docs]);

  function addFiles(files: FileList | File[]) {
    const next = Array.from(files).map(file => {
      const ext = extOf(file.name);
      let rejection: string | null = null;
      if (!ACCEPTED_EXTENSIONS.includes(ext)) {
        rejection = `Unsupported file type (${ext || 'no extension'})`;
      } else if (file.size > MAX_SIZE_BYTES) {
        rejection = `Too large — ${(file.size / 1024 / 1024).toFixed(1)} MB exceeds the 30 MB limit`;
      }
      return { file, rejection };
    });
    setQueuedFiles(prev => [...prev, ...next]);
    const acceptedCount = next.filter(f => !f.rejection).length;
    if (acceptedCount > 0) {
      getTranslationQuota(acceptedCount).then(setQuota).catch(() => setQuota(null));
    }
  }

  function removeQueued(index: number) {
    setQueuedFiles(prev => {
      const next = prev.filter((_, i) => i !== index);
      const acceptedCount = next.filter(f => !f.rejection).length;
      if (acceptedCount > 0) getTranslationQuota(acceptedCount).then(setQuota).catch(() => setQuota(null));
      else setQuota(null);
      return next;
    });
  }

  async function uploadQueued() {
    const toUpload = queuedFiles.filter(f => !f.rejection);
    if (toUpload.length === 0) return;
    setUploading(true);
    setError('');
    try {
      for (const { file } of toUpload) {
        await uploadTranslatedDocument(file);
      }
      setQueuedFiles([]);
      setQuota(null);
      if (fileInputRef.current) fileInputRef.current.value = '';
      load();
    } catch {
      setError('One or more uploads failed — the file may be unsupported, too large, or the quota reserve was hit');
    } finally {
      setUploading(false);
    }
  }

  async function withBusy(id: number, fn: () => Promise<void>) {
    setBusyIds(prev => new Set(prev).add(id));
    try {
      await fn();
    } finally {
      setBusyIds(prev => { const next = new Set(prev); next.delete(id); return next; });
    }
  }

  async function retry(doc: TranslatedDocument) {
    await withBusy(doc.id, async () => { await retryTranslatedDocument(doc.id); load(); });
  }

  async function remove(doc: TranslatedDocument) {
    await withBusy(doc.id, async () => {
      await deleteTranslatedDocument(doc.id);
      setSelected(prev => { const next = new Set(prev); next.delete(doc.id); return next; });
      load();
    });
  }

  async function saveToVault(doc: TranslatedDocument) {
    await withBusy(doc.id, async () => { await saveTranslatedDocumentToVault(doc.id); });
  }

  async function downloadOne(doc: TranslatedDocument, version: 'original' | 'translated') {
    const blob = await downloadTranslatedDocumentBlob(doc.id, version);
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = doc.filename;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  }

  async function downloadSelected() {
    if (selected.size === 0) return;
    const blob = await downloadAllTranslatedDocumentsBlob(Array.from(selected));
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'translated_documents.zip';
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  }

  function toggleSelected(id: number) {
    setSelected(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  if (loading) return <div className="loading">Loading…</div>;

  return (
    <div>
      <h1 style={{ fontSize: 28, fontWeight: 700, marginBottom: 4 }}>Translate</h1>
      <p style={{ color: '#8892a4', marginBottom: 16 }}>
        Drop one or more documents to translate them to English (EN-GB), formatting preserved — original
        stays alongside.
      </p>
      {error && <div className="error" style={{ marginBottom: 12 }}>{error}</div>}
      {deeplDown && (
        <div style={{ background: 'rgba(248,113,113,0.08)', border: '1px solid rgba(248,113,113,0.3)', borderRadius: 8, padding: '10px 14px', marginBottom: 16, color: '#f87171', fontSize: 13 }}>
          Translation is currently unavailable (DeepL is failing) — uploads are disabled until it recovers. Check Portal Health → Scout → Portals & Health.
        </div>
      )}

      {/* Drop zone */}
      <div
        className="card"
        onDragOver={e => { e.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={e => {
          e.preventDefault();
          setDragOver(false);
          if (e.dataTransfer.files.length) addFiles(e.dataTransfer.files);
        }}
        style={{
          padding: 28, marginBottom: 20, textAlign: 'center', cursor: 'pointer',
          border: `2px dashed ${dragOver ? '#2EE6D4' : '#1a2334'}`,
          background: dragOver ? 'rgba(46,230,212,0.04)' : undefined,
        }}
        onClick={() => fileInputRef.current?.click()}
      >
        <input
          ref={fileInputRef}
          type="file"
          multiple
          style={{ display: 'none' }}
          onChange={e => { if (e.target.files?.length) addFiles(e.target.files); }}
        />
        <div style={{ fontSize: 14, color: '#cdd6e3', marginBottom: 6 }}>
          Drag and drop files here, or click to browse
        </div>
        <div style={{ fontSize: 12, color: '#4c5a70' }}>
          PDF, DOCX, PPTX, XLSX, TXT, HTML, XLF, SRT — up to 30 MB per file
        </div>
      </div>

      {/* Queued files pending upload */}
      {queuedFiles.length > 0 && (
        <div className="card" style={{ padding: 16, marginBottom: 20 }}>
          <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.08em', color: '#4c5a70', textTransform: 'uppercase', marginBottom: 10 }}>
            Ready to translate
          </div>
          {queuedFiles.map((f, i) => (
            <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '6px 0', borderBottom: i < queuedFiles.length - 1 ? '1px solid #1a2334' : 'none' }}>
              <span style={{ fontSize: 13, flex: 1 }}>{f.file.name}</span>
              <span style={{ fontSize: 11, color: '#4c5a70' }}>{(f.file.size / 1024).toFixed(0)} KB</span>
              {f.rejection && <span style={{ fontSize: 12, color: '#f87171' }}>{f.rejection}</span>}
              <button className="btn-ghost" style={{ fontSize: 11, border: 'none' }} onClick={() => removeQueued(i)}>✕</button>
            </div>
          ))}
          {quota && (
            <div style={{ marginTop: 10, fontSize: 12, color: quota.allowed ? '#8892a4' : '#f87171' }}>
              Estimated cost: {quota.estimated_cost.toLocaleString()} characters.{' '}
              {quota.remaining_documents !== null
                ? `~${quota.remaining_documents} document${quota.remaining_documents === 1 ? '' : 's'} of headroom remaining.`
                : 'Remaining allowance unavailable right now.'}
              {!quota.allowed && quota.reason && <> — {quota.reason}</>}
            </div>
          )}
          <button
            className="btn btn-teal"
            style={{ marginTop: 12 }}
            disabled={uploading || deeplDown || queuedFiles.every(f => f.rejection) || (quota ? !quota.allowed : false)}
            onClick={uploadQueued}
          >
            {uploading ? 'Translating…' : 'Translate to English'}
          </button>
        </div>
      )}

      {/* Filters + bulk actions */}
      <div style={{ display: 'flex', gap: 10, marginBottom: 16, alignItems: 'center', flexWrap: 'wrap' }}>
        <select value={statusFilter} onChange={e => setStatusFilter(e.target.value)}>
          <option value="">All statuses</option>
          <option value="queued">Queued</option>
          <option value="translating">Translating</option>
          <option value="ready">Ready</option>
          <option value="already_english">Already English</option>
          <option value="failed">Failed</option>
        </select>
        {selected.size > 0 && (
          <button className="btn btn-ghost" style={{ fontSize: 12 }} onClick={downloadSelected}>
            ⤓ Download {selected.size} selected (zip)
          </button>
        )}
      </div>

      {/* History */}
      {docs.length === 0 ? (
        <div className="card" style={{ padding: 32, textAlign: 'center', color: '#8892a4' }}>No translation jobs yet.</div>
      ) : (
        <div className="card">
          {docs.map(doc => {
            const busy = busyIds.has(doc.id);
            return (
              <div key={doc.id} style={{ padding: '14px 16px', borderBottom: '1px solid #1a2334', opacity: busy ? 0.6 : 1 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                  <input type="checkbox" checked={selected.has(doc.id)} onChange={() => toggleSelected(doc.id)} />
                  <span style={{ fontWeight: 600, fontSize: 13 }}>{doc.filename}</span>
                  <StatusBadge status={doc.status} />
                  <span style={{ color: '#8892a4', fontSize: 11 }}>
                    {doc.source_lang || '?'} → {doc.status === 'already_english' ? '—' : doc.target_lang}
                  </span>
                  <span style={{ color: '#8892a4', fontSize: 11 }}>
                    {doc.uploaded_by} · {formatDate(doc.uploaded_at)}
                  </span>
                  {doc.billed_characters != null && (
                    <span style={{ color: '#4c5a70', fontSize: 11 }}>{doc.billed_characters.toLocaleString()} chars</span>
                  )}
                  <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
                    <button className="btn btn-ghost" disabled={busy} style={{ fontSize: 12, padding: '4px 10px' }} onClick={() => downloadOne(doc, 'original')}>
                      Original
                    </button>
                    {doc.status === 'ready' && (
                      <button className="btn btn-ghost" disabled={busy} style={{ fontSize: 12, padding: '4px 10px' }} onClick={() => downloadOne(doc, 'translated')}>
                        Translated
                      </button>
                    )}
                    {(doc.status === 'ready' || doc.status === 'already_english') && (
                      <button className="btn btn-ghost" disabled={busy} style={{ fontSize: 12, padding: '4px 10px' }} onClick={() => saveToVault(doc)}>
                        Save to Vault
                      </button>
                    )}
                    {doc.status === 'failed' && (
                      <button className="btn btn-ghost" disabled={busy} style={{ fontSize: 12, padding: '4px 10px', color: '#2EE6D4' }} onClick={() => retry(doc)}>
                        Retry
                      </button>
                    )}
                    <button className="btn btn-ghost" disabled={busy} style={{ fontSize: 12, padding: '4px 10px', color: '#f87171' }} onClick={() => remove(doc)}>
                      Delete
                    </button>
                  </div>
                </div>
                {doc.status === 'failed' && doc.error_message && (
                  <div style={{ marginTop: 6, color: '#f87171', fontSize: 12 }}>{doc.error_message}</div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
