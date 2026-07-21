import { useEffect, useState, useRef } from 'react';
import { getPipeline, patchPipeline, listDocuments, uploadDocument, downloadDocumentBlob } from '../../api';
import type { PipelineEntry, DocumentEntry } from '../../types';
import { formatDate, daysLeft, countryFlag, hasTranslatedTagLine, displayTagLine } from '../../utils';
import { useNavigate } from '../../router';

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function deadlineDotColor(e: PipelineEntry): string {
  const dl = e.deadline_override || e.deadline;
  const d = daysLeft(dl);
  if (d === null) return '#4c5a70';
  if (d <= 7) return '#f87171';
  if (d <= 14) return '#e3b341';
  return '#34d399';
}

function daysLeftLabel(dl: string | null | undefined): string {
  const d = daysLeft(dl);
  if (d === null) return '—';
  if (d < 0) return `${Math.abs(d)} day${Math.abs(d) !== 1 ? 's' : ''} overdue`;
  if (d === 0) return 'today';
  return `${d} day${d !== 1 ? 's' : ''} left`;
}

export default function PortalPipeline() {
  const navigate = useNavigate();
  const [pipeline, setPipeline] = useState<PipelineEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [selected, setSelected] = useState<PipelineEntry | null>(null);
  const [amendOpen, setAmendOpen] = useState(false);
  const [amendValue, setAmendValue] = useState('');
  const [notes, setNotes] = useState('');
  const [owner, setOwner] = useState('');
  const notesTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const ownerTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // CR-002 E: minimal document upload slice
  const [documents, setDocuments] = useState<DocumentEntry[]>([]);
  const [uploading, setUploading] = useState(false);
  const [docError, setDocError] = useState('');

  function load() {
    getPipeline()
      .then(data => {
        setPipeline(data);
        setSelected(prev => {
          if (!prev) return data[0] ?? null;
          const updated = data.find(e => e.pub_number === prev.pub_number);
          return updated ?? data[0] ?? null;
        });
      })
      .catch(() => setError('Failed to load pipeline'))
      .finally(() => setLoading(false));
  }

  useEffect(() => { load(); }, []);

  useEffect(() => {
    if (selected) {
      setNotes(selected.notes ?? '');
      setOwner(selected.owner ?? '');
      setAmendOpen(false);
      setAmendValue('');
      loadDocuments(selected.pub_number);
    }
  }, [selected?.pub_number]);

  function loadDocuments(pub_number: string) {
    setDocError('');
    listDocuments(pub_number).then(setDocuments).catch(() => setDocError('Failed to load documents'));
  }

  async function handleUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file || !selected) return;
    setUploading(true);
    setDocError('');
    try {
      await uploadDocument(selected.pub_number, file);
      loadDocuments(selected.pub_number);
    } catch {
      setDocError('Upload failed — try again.');
    } finally {
      setUploading(false);
    }
  }

  async function handleDownload(doc: DocumentEntry) {
    try {
      const blob = await downloadDocumentBlob(doc.id);
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = doc.filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch {
      setDocError('Download failed — try again.');
    }
  }

  const closing = pipeline.filter(e => {
    const d = daysLeft(e.deadline_override || e.deadline);
    return d !== null && d <= 7 && e.submission_status !== 'submitted';
  });

  async function setStatus(status: string) {
    if (!selected) return;
    await patchPipeline(selected.pub_number, { submission_status: status });
    load();
  }

  async function applyAmend() {
    if (!selected || !amendValue) return;
    await patchPipeline(selected.pub_number, { deadline_override: amendValue });
    setAmendOpen(false);
    load();
  }

  function handleNotesBlur() {
    if (!selected) return;
    if (notesTimer.current) clearTimeout(notesTimer.current);
    patchPipeline(selected.pub_number, { notes }).catch(() => {});
  }

  function handleOwnerBlur() {
    if (!selected) return;
    if (ownerTimer.current) clearTimeout(ownerTimer.current);
    patchPipeline(selected.pub_number, { owner }).catch(() => {});
  }

  if (loading) return <div className="loading">Loading…</div>;
  if (error) return <div className="error">{error}</div>;

  const selDl = selected ? (selected.deadline_override || selected.deadline) : null;
  const selDays = daysLeft(selDl);
  const isUrgent = selected && selDays !== null && selDays <= 7 && selected.submission_status !== 'submitted';
  const isWarning = selected && selDays !== null && selDays <= 14 && selDays > 7 && selected.submission_status !== 'submitted';

  return (
    <div>
      <h1 style={{ fontSize: 28, fontWeight: 700, marginBottom: 4 }}>Pipeline & Deadlines</h1>
      <p style={{ color: '#8892a4', marginBottom: 16 }}>Accepted tenders in progress — track submission status, amend deadlines, keep notes</p>

      <div style={{ display: 'flex', gap: 10, marginBottom: 20 }}>
        {closing.length > 0 && <span className="pill pill-red">🔴 {closing.length} closing soon</span>}
        <span className="pill pill-grey">{pipeline.length - closing.length} open</span>
      </div>

      {pipeline.length === 0 ? (
        <div className="card" style={{ padding: 32, textAlign: 'center', color: '#8892a4' }}>
          No accepted tenders yet. Shortlist tenders in the Review Queue to see them here.
        </div>
      ) : (
        <div style={{ display: 'flex', gap: 16, alignItems: 'flex-start' }}>
          {/* Left list */}
          <div className="card" style={{ width: '40%', minWidth: 260, flexShrink: 0 }}>
            <div style={{ padding: '12px 16px', borderBottom: '1px solid #1a2334', fontSize: 11, fontWeight: 700, letterSpacing: '0.08em', color: '#8892a4', textTransform: 'uppercase' }}>
              Accepted · {pipeline.length} Tenders
            </div>
            {pipeline.map(e => {
              const dotColor = deadlineDotColor(e);
              const dl = e.deadline_override || e.deadline;
              const isActive = selected?.pub_number === e.pub_number;
              return (
                <div
                  key={e.pub_number}
                  onClick={() => setSelected(e)}
                  style={{
                    padding: '12px 16px', cursor: 'pointer', borderBottom: '1px solid #1a2334',
                    background: isActive ? 'rgba(46,230,212,0.05)' : 'transparent',
                    borderLeft: `3px solid ${isActive ? '#2EE6D4' : 'transparent'}`,
                    display: 'flex', alignItems: 'flex-start', gap: 10,
                  }}
                >
                  <div style={{ width: 8, height: 8, borderRadius: '50%', background: dotColor, marginTop: 5, flexShrink: 0 }} />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontWeight: 500, fontSize: 13, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: isActive ? '#e2e8f0' : '#c8d0de' }}>
                      {hasTranslatedTagLine(e) && (
                        <span title={`Translated — original: ${e.tag_line}`} style={{ marginRight: 4 }}>🌐</span>
                      )}
                      {displayTagLine(e)}
                    </div>
                    <div style={{ color: '#8892a4', fontSize: 11, display: 'flex', justifyContent: 'space-between', marginTop: 2 }}>
                      <span>{e.source}</span>
                      <span className="mono">{formatDate(dl)}</span>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>

          {/* Right detail */}
          {selected && (
            <div className="card" style={{ flex: 1, minWidth: 0 }}>
              {/* Urgency banner */}
              {isUrgent && (
                <div style={{ background: 'rgba(248,113,113,0.1)', border: '1px solid rgba(248,113,113,0.3)', borderRadius: '8px 8px 0 0', padding: '10px 16px', display: 'flex', gap: 10, alignItems: 'flex-start' }}>
                  <span style={{ color: '#f87171', fontSize: 16 }}>⚠</span>
                  <span style={{ color: '#f87171', fontSize: 13, fontWeight: 500 }}>
                    Closes in {selDays} day{selDays !== 1 ? 's' : ''} and no tender has been sent — act now or request an extension.
                  </span>
                </div>
              )}
              {isWarning && (
                <div style={{ background: 'rgba(227,179,65,0.1)', border: '1px solid rgba(227,179,65,0.3)', borderRadius: '8px 8px 0 0', padding: '10px 16px', display: 'flex', gap: 10, alignItems: 'flex-start' }}>
                  <span style={{ color: '#e3b341', fontSize: 16 }}>⚠</span>
                  <span style={{ color: '#e3b341', fontSize: 13, fontWeight: 500 }}>
                    Deadline in {selDays} day{selDays !== 1 ? 's' : ''} — review submission progress.
                  </span>
                </div>
              )}

              <div style={{ padding: '20px 24px' }}>
                {/* Header */}
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10, color: '#8892a4', fontSize: 13 }}>
                  <span>{countryFlag(selected.country)}</span>
                  <span>{selected.source}</span>
                  <span>·</span>
                  <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{selected.buyer}</span>
                </div>
                <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12, marginBottom: 20 }}>
                  <h2 style={{ fontSize: 22, fontWeight: 700, lineHeight: 1.3 }}>{displayTagLine(selected)}</h2>
                  <button
                    className="btn btn-purple-solid"
                    style={{ flexShrink: 0, whiteSpace: 'nowrap', fontSize: 12 }}
                    onClick={() => navigate(`/composer/ingest?pub=${encodeURIComponent(selected.pub_number)}`)}
                  >
                    ✦ Draft proposal in Composer
                  </button>
                </div>

                {/* Deadline */}
                <div style={{ marginBottom: 20 }}>
                  <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.08em', color: '#4c5a70', textTransform: 'uppercase', marginBottom: 8 }}>Deadline</div>
                  <div style={{ display: 'flex', alignItems: 'baseline', gap: 12 }}>
                    <span className="mono" style={{ fontSize: 28, fontWeight: 700, color: '#e2e8f0' }}>
                      {formatDate(selDl)}
                    </span>
                    <span style={{ color: selDays !== null && selDays < 0 ? '#f87171' : '#8892a4', fontSize: 13 }}>
                      {daysLeftLabel(selDl)}
                    </span>
                  </div>
                  {selected.deadline_override && (
                    <div style={{ marginTop: 4, fontSize: 12, color: '#e3b341' }}>
                      Extended from original: {formatDate(selected.deadline)} <span style={{ background: 'rgba(227,179,65,0.15)', padding: '1px 6px', borderRadius: 4 }}>extended</span>
                    </div>
                  )}
                </div>

                {/* Amend deadline */}
                {!amendOpen ? (
                  <button
                    onClick={() => setAmendOpen(true)}
                    className="btn btn-ghost"
                    style={{ marginBottom: 20, fontSize: 13 }}
                  >
                    + Amend deadline (extension granted)
                  </button>
                ) : (
                  <div style={{ marginBottom: 20, display: 'flex', gap: 8, alignItems: 'center' }}>
                    <input
                      type="date"
                      className="input-field"
                      style={{ width: 180 }}
                      value={amendValue}
                      onChange={e => setAmendValue(e.target.value)}
                    />
                    <button className="btn btn-teal" onClick={applyAmend} style={{ fontSize: 13 }}>Save</button>
                    <button className="btn btn-ghost" onClick={() => setAmendOpen(false)} style={{ fontSize: 13 }}>Cancel</button>
                  </div>
                )}

                {/* Submission status */}
                <div style={{ marginBottom: 20 }}>
                  <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.08em', color: '#4c5a70', textTransform: 'uppercase', marginBottom: 10 }}>Submission Status</div>
                  <div style={{ display: 'flex', gap: 8 }}>
                    {(['not_started', 'drafting', 'submitted'] as const).map(s => {
                      const labels: Record<string, string> = { not_started: 'Not started', drafting: 'Drafting', submitted: 'Tender sent' };
                      const isActive = selected.submission_status === s;
                      return (
                        <button
                          key={s}
                          onClick={() => setStatus(s)}
                          style={{
                            padding: '7px 14px', borderRadius: 6, fontSize: 13, fontWeight: 500,
                            border: `1px solid ${isActive ? '#2EE6D4' : '#1a2334'}`,
                            background: isActive ? 'rgba(46,230,212,0.12)' : 'transparent',
                            color: isActive ? '#2EE6D4' : '#8892a4',
                          }}
                        >
                          {labels[s]}
                        </button>
                      );
                    })}
                  </div>
                </div>

                {/* Owner */}
                <div style={{ marginBottom: 16 }}>
                  <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.08em', color: '#4c5a70', textTransform: 'uppercase', marginBottom: 8 }}>Owner</div>
                  <input
                    className="input-field"
                    style={{ maxWidth: 280 }}
                    placeholder="Assign owner…"
                    value={owner}
                    onChange={e => setOwner(e.target.value)}
                    onBlur={handleOwnerBlur}
                  />
                </div>

                {/* Notes */}
                <div style={{ marginBottom: 20 }}>
                  <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.08em', color: '#4c5a70', textTransform: 'uppercase', marginBottom: 8 }}>Notes</div>
                  <textarea
                    className="input-field"
                    style={{ minHeight: 100, resize: 'vertical' }}
                    placeholder="Add notes…"
                    value={notes}
                    onChange={e => setNotes(e.target.value)}
                    onBlur={handleNotesBlur}
                  />
                </div>

                {/* CR-002 E: documents — minimal upload slice, upload + store only */}
                <div>
                  <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.08em', color: '#4c5a70', textTransform: 'uppercase', marginBottom: 8 }}>Documents</div>
                  {documents.length === 0 ? (
                    <div style={{ color: '#8892a4', fontSize: 13, marginBottom: 10 }}>No documents uploaded yet.</div>
                  ) : (
                    <div style={{ marginBottom: 10 }}>
                      {documents.map(doc => (
                        <div key={doc.id} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '6px 0', borderBottom: '1px solid #1a2334' }}>
                          <span style={{ fontSize: 13, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{doc.filename}</span>
                          <span style={{ color: '#8892a4', fontSize: 11 }}>{formatSize(doc.size)}</span>
                          <button className="btn btn-ghost" style={{ fontSize: 12, padding: '3px 8px' }} onClick={() => handleDownload(doc)}>
                            ⤓ Download
                          </button>
                        </div>
                      ))}
                    </div>
                  )}
                  <label className="btn btn-ghost" style={{ fontSize: 12, display: 'inline-block', cursor: uploading ? 'default' : 'pointer', opacity: uploading ? 0.6 : 1 }}>
                    {uploading ? 'Uploading…' : '+ Upload document'}
                    <input type="file" onChange={handleUpload} disabled={uploading} style={{ display: 'none' }} />
                  </label>
                  {docError && <div style={{ color: '#f87171', fontSize: 12, marginTop: 8 }}>{docError}</div>}
                </div>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
