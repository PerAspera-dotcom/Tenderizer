import { useEffect, useRef, useState } from 'react';
import {
  getTranslationQuota, uploadTranslatedDocument, listTranslatedDocuments,
  downloadTranslatedDocumentBlob, attachTranslatedDocument, detachTranslatedDocument,
} from '../../api';
import type { TranslatedDocument, TranslationQuota } from '../../types';
import { formatDate } from '../../utils';

// Statuses a background poll should keep checking — translation is async
// (upload -> poll -> download on DeepL's side), never a hanging spinner.
const IN_PROGRESS = new Set(['queued', 'translating']);

const STATUS_STYLE: Record<string, { bg: string; fg: string; label: string }> = {
  queued: { bg: 'rgba(148,163,184,0.1)', fg: '#94a3b8', label: 'Queued' },
  translating: { bg: 'rgba(96,165,250,0.1)', fg: '#60a5fa', label: 'Translating…' },
  ready: { bg: 'rgba(52,211,153,0.1)', fg: '#34d399', label: 'Ready' },
  failed: { bg: 'rgba(248,113,113,0.1)', fg: '#f87171', label: 'Failed' },
};

function StatusBadge({ status }: { status: string }) {
  const s = STATUS_STYLE[status] ?? STATUS_STYLE.queued;
  return (
    <span style={{ background: s.bg, color: s.fg, border: `1px solid ${s.fg}55`, borderRadius: 9999, padding: '2px 10px', fontSize: 11, fontWeight: 600 }}>
      ● {s.label}
    </span>
  );
}

export default function TranslatedDocuments() {
  const [docs, setDocs] = useState<TranslatedDocument[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [languageFilter, setLanguageFilter] = useState('');
  const [attachedFilter, setAttachedFilter] = useState<'' | 'true' | 'false'>('');

  const [targetLang, setTargetLang] = useState('EN');
  const [pendingFile, setPendingFile] = useState<File | null>(null);
  const [quote, setQuote] = useState<TranslationQuota | null>(null);
  const [uploading, setUploading] = useState(false);
  const [attachInputs, setAttachInputs] = useState<Record<number, string>>({});
  const fileInputRef = useRef<HTMLInputElement>(null);

  function load() {
    listTranslatedDocuments({
      status: statusFilter || undefined,
      language: languageFilter || undefined,
      attached: attachedFilter === '' ? undefined : attachedFilter === 'true',
    }).then(setDocs).catch(() => setError('Failed to load translated documents')).finally(() => setLoading(false));
  }

  useEffect(() => { load(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [statusFilter, languageFilter, attachedFilter]);

  // Poll while anything is still queued/translating — real progress states,
  // not a spinner that hangs (translation_command.md's own requirement).
  useEffect(() => {
    if (!docs.some(d => IN_PROGRESS.has(d.status))) return;
    const t = setTimeout(load, 4000);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [docs]);

  async function onFileChosen(file: File) {
    setPendingFile(file);
    setQuote(await getTranslationQuota(1).catch(() => null));
  }

  async function confirmUpload() {
    if (!pendingFile) return;
    setUploading(true);
    try {
      await uploadTranslatedDocument(pendingFile, targetLang);
      setPendingFile(null);
      setQuote(null);
      if (fileInputRef.current) fileInputRef.current.value = '';
      load();
    } catch {
      setError('Upload failed — the file may be unsupported or too large');
    } finally {
      setUploading(false);
    }
  }

  async function openVersion(doc: TranslatedDocument, version: 'original' | 'translated') {
    const blob = await downloadTranslatedDocumentBlob(doc.id, version);
    const url = URL.createObjectURL(blob);
    window.open(url, '_blank');
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  }

  async function attach(doc: TranslatedDocument) {
    const pub = (attachInputs[doc.id] || '').trim();
    if (!pub) return;
    await attachTranslatedDocument(doc.id, pub);
    setAttachInputs(prev => ({ ...prev, [doc.id]: '' }));
    load();
  }

  async function detach(doc: TranslatedDocument, pub: string) {
    await detachTranslatedDocument(doc.id, pub);
    load();
  }

  if (loading) return <div className="loading">Loading…</div>;

  return (
    <div>
      <h1 style={{ fontSize: 28, fontWeight: 700, marginBottom: 4 }}>Translated Documents</h1>
      <p style={{ color: '#8892a4', marginBottom: 16 }}>
        Upload a buyer's tender document for translation, attach it to one or more tenders, and find it again here.
      </p>
      {error && <div className="error" style={{ marginBottom: 12 }}>{error}</div>}

      {/* Upload */}
      <div className="card" style={{ padding: 16, marginBottom: 20 }}>
        <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.08em', color: '#4c5a70', textTransform: 'uppercase', marginBottom: 10 }}>
          Translate a document
        </div>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
          <input
            ref={fileInputRef}
            type="file"
            onChange={e => { const f = e.target.files?.[0]; if (f) onFileChosen(f); }}
          />
          <label style={{ fontSize: 12, color: '#8892a4' }}>
            Target language{' '}
            <select value={targetLang} onChange={e => setTargetLang(e.target.value)} style={{ marginLeft: 4 }}>
              <option value="EN">English</option>
              <option value="FR">French</option>
              <option value="DE">German</option>
              <option value="ES">Spanish</option>
            </select>
          </label>
          <button className="btn" disabled={!pendingFile || uploading || (quote ? !quote.allowed : false)} onClick={confirmUpload}>
            {uploading ? 'Uploading…' : 'Translate'}
          </button>
        </div>
        {quote && (
          <div style={{ marginTop: 10, fontSize: 12, color: quote.allowed ? '#8892a4' : '#f87171' }}>
            Estimated cost: {quote.estimated_cost.toLocaleString()} characters.{' '}
            {quote.remaining_documents !== null
              ? `~${quote.remaining_documents} document${quote.remaining_documents === 1 ? '' : 's'} of headroom remaining this period.`
              : 'Remaining allowance unavailable right now.'}
            {!quote.allowed && quote.reason && <> — {quote.reason}</>}
          </div>
        )}
      </div>

      {/* Filters */}
      <div style={{ display: 'flex', gap: 10, marginBottom: 16, alignItems: 'center', flexWrap: 'wrap' }}>
        <select value={statusFilter} onChange={e => setStatusFilter(e.target.value)}>
          <option value="">All statuses</option>
          <option value="queued">Queued</option>
          <option value="translating">Translating</option>
          <option value="ready">Ready</option>
          <option value="failed">Failed</option>
        </select>
        <input
          placeholder="Filter by language (e.g. FR)"
          value={languageFilter}
          onChange={e => setLanguageFilter(e.target.value.toUpperCase())}
          style={{ width: 200 }}
        />
        <select value={attachedFilter} onChange={e => setAttachedFilter(e.target.value as '' | 'true' | 'false')}>
          <option value="">Attached or not</option>
          <option value="true">Attached</option>
          <option value="false">Unattached</option>
        </select>
      </div>

      {/* List */}
      {docs.length === 0 ? (
        <div className="card" style={{ padding: 32, textAlign: 'center', color: '#8892a4' }}>No translated documents yet.</div>
      ) : (
        <div className="card">
          {docs.map(doc => (
            <div key={doc.id} style={{ padding: '14px 16px', borderBottom: '1px solid #1a2334' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                <span style={{ fontWeight: 600, fontSize: 13 }}>{doc.filename}</span>
                <StatusBadge status={doc.status} />
                <span style={{ color: '#8892a4', fontSize: 11 }}>
                  {doc.source_lang || '?'} → {doc.target_lang}
                </span>
                <span style={{ color: '#8892a4', fontSize: 11 }}>
                  {doc.uploaded_by} · {formatDate(doc.uploaded_at)}
                </span>
                <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
                  <button className="btn btn-ghost" style={{ fontSize: 12, padding: '4px 10px' }} onClick={() => openVersion(doc, 'original')}>
                    Original
                  </button>
                  {doc.status === 'ready' && (
                    <button className="btn btn-ghost" style={{ fontSize: 12, padding: '4px 10px' }} onClick={() => openVersion(doc, 'translated')}>
                      Translated
                    </button>
                  )}
                </div>
              </div>

              {doc.status === 'failed' && doc.error_message && (
                <div style={{ marginTop: 6, color: '#f87171', fontSize: 12 }}>{doc.error_message}</div>
              )}

              <div style={{ marginTop: 8, display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
                {doc.attached_tenders.map(pub => (
                  <span key={pub} className="pill" style={{ fontSize: 11, display: 'flex', gap: 6, alignItems: 'center' }}>
                    {pub}
                    <button className="btn-ghost" style={{ fontSize: 11, padding: 0, border: 'none' }} onClick={() => detach(doc, pub)}>✕</button>
                  </span>
                ))}
                <input
                  placeholder="Attach to tender (pub number)"
                  value={attachInputs[doc.id] || ''}
                  onChange={e => setAttachInputs(prev => ({ ...prev, [doc.id]: e.target.value }))}
                  style={{ fontSize: 12, width: 200 }}
                />
                <button className="btn btn-ghost" style={{ fontSize: 12, padding: '4px 10px' }} onClick={() => attach(doc)}>
                  Attach
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
