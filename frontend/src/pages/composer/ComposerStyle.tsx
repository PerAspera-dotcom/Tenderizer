import { useEffect, useRef, useState } from 'react';
import {
  getComposerStyle, putComposerStyle, listStyleExamples, uploadStyleExample,
  deleteStyleExample, extractComposerStyle,
} from '../../api';
import type { ComposerStyleGuide, ComposerStyleExample } from '../../types';

export default function ComposerStyle() {
  const [guide, setGuide] = useState<ComposerStyleGuide | null>(null);
  const [examples, setExamples] = useState<ComposerStyleExample[]>([]);
  const [draft, setDraft] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [uploading, setUploading] = useState(false);
  const [extracting, setExtracting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  function refresh() {
    return Promise.all([getComposerStyle(), listStyleExamples()])
      .then(([g, ex]) => { setGuide(g); setDraft(g.style_guide ?? ''); setExamples(ex.results); setError(''); })
      .catch(() => setError('Failed to load style guide'))
      .finally(() => setLoading(false));
  }

  useEffect(() => { refresh(); }, []);

  async function handleFilesSelected(files: FileList | null) {
    if (!files || files.length === 0) return;
    setUploading(true);
    try {
      for (const file of Array.from(files)) {
        await uploadStyleExample(file);
      }
      await refresh();
    } catch {
      setError('Upload failed — try again');
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  }

  async function handleDelete(id: number) {
    await deleteStyleExample(id);
    setExamples(ex => ex.filter(e => e.id !== id));
  }

  async function handleExtract() {
    setExtracting(true);
    setError('');
    try {
      const g = await extractComposerStyle();
      setGuide(g);
      setDraft(g.style_guide ?? '');
    } catch {
      setError('Extraction failed — check at least one example is uploaded and Claude is configured');
    } finally {
      setExtracting(false);
    }
  }

  async function handleSave() {
    setSaving(true);
    setError('');
    try {
      const g = await putComposerStyle(draft);
      setGuide(g);
      setSaved(true);
    } catch {
      setError('Failed to save — try again');
    } finally {
      setSaving(false);
    }
  }

  if (loading) return <div className="loading">Loading…</div>;

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 6, flexWrap: 'wrap' }}>
        <h1 style={{ fontSize: 28, fontWeight: 700 }}>Style Guide</h1>
        <span style={{ background: 'rgba(192,132,252,0.15)', color: '#c084fc', border: '1px solid rgba(192,132,252,0.3)', borderRadius: 6, fontSize: 11, fontWeight: 700, padding: '3px 8px', letterSpacing: '0.06em' }}>
          COMPOSER
        </span>
      </div>
      <p style={{ color: '#8a97ac', marginBottom: 20, fontSize: 13 }}>
        Upload a few of your team's prior proposals — Composer extracts a reusable house style
        (tone, compliance-language conventions, sentence patterns) and applies it to every future
        generated draft.
      </p>

      {error && <div style={{ color: '#f87171', fontSize: 13, marginBottom: 12 }}>{error}</div>}

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1.4fr', gap: 16, alignItems: 'start' }}>
        {/* Left — example proposals */}
        <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '14px 16px', borderBottom: '1px solid #1f2b40' }}>
            <span style={{ fontWeight: 600, fontSize: 13 }}>Example proposals</span>
            <span style={{ color: '#8a97ac', fontSize: 12 }}>{examples.length} uploaded</span>
          </div>
          {examples.length === 0 ? (
            <div style={{ padding: 24, textAlign: 'center', color: '#8a97ac', fontSize: 13 }}>
              No examples yet — upload one or more prior proposals to learn from.
            </div>
          ) : examples.map(ex => (
            <div key={ex.id} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '10px 16px', borderBottom: '1px solid #1b2536' }}>
              <span className="mono" style={{ fontSize: 12, color: '#cdd6e3', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {ex.filename}
              </span>
              <button className="btn btn-ghost" style={{ fontSize: 11, padding: '2px 8px' }} onClick={() => handleDelete(ex.id)}>
                Remove
              </button>
            </div>
          ))}
          <div style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 8 }}>
            <input
              ref={fileInputRef}
              type="file"
              accept=".pdf,.docx"
              multiple
              style={{ display: 'none' }}
              onChange={e => handleFilesSelected(e.target.files)}
            />
            <button className="btn btn-purple" disabled={uploading} onClick={() => fileInputRef.current?.click()}>
              {uploading ? 'Uploading…' : '⤓ Upload examples'}
            </button>
            <button className="btn btn-purple-solid" disabled={extracting || examples.length === 0} onClick={handleExtract}>
              {extracting ? 'Extracting…' : 'Extract style'}
            </button>
          </div>
        </div>

        {/* Right — the style guide itself */}
        <div className="card" style={{ padding: '16px', display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <span style={{ fontWeight: 600, fontSize: 13 }}>Guide</span>
            {guide?.generated_at && (
              <span style={{ color: '#8a97ac', fontSize: 11 }}>
                Last extracted from {guide.source_doc_count} example{guide.source_doc_count === 1 ? '' : 's'} · {new Date(guide.generated_at).toLocaleString()}
              </span>
            )}
          </div>
          <textarea
            className="input-field"
            style={{ minHeight: 320, fontFamily: 'inherit', fontSize: 13, lineHeight: 1.5, resize: 'vertical' }}
            placeholder="Extract from examples, or write/paste a style guide directly."
            value={draft}
            onChange={e => { setDraft(e.target.value); setSaved(false); }}
          />
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <button className="btn btn-teal" onClick={handleSave} disabled={saving}>
              {saving ? 'Saving…' : 'Save changes'}
            </button>
            {saved && <span style={{ color: '#34d399', fontSize: 13 }}>Saved</span>}
          </div>
        </div>
      </div>
    </div>
  );
}
