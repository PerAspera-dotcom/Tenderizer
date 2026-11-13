// CR-013 §4/§5 — manual tender entry. Three ways onto one form:
//   /scout/add-tender               type it, or upload documents to read
//   /scout/add-tender?draft=12      review what was read from the documents
//   /scout/add-tender?edit=MAN-…    edit an existing manual tender
//
// The user supplies only the fields below. pub_number, source, created_by and
// every derived field (matching, EUR value, exclusion, translation) are set by
// the server through the same code path as a scraped tender. Nothing read from
// a document is saved until the person presses the button: high-confidence
// values arrive pre-filled and marked "from document"; low-confidence ones
// arrive as suggestions beside an empty field.
import { useEffect, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from '../../router';
import {
  addManualDraftFile, addOriginSite, ApiError, confirmManualDraft, createManualDraft, createManualTender,
  discardManualDraft, getManualDraft, getOriginSites, getTender, listManualDrafts,
  startManualDraftExtraction, updateManualTender,
} from '../../api';
import type { ExtractedField, ManualDraft, ManualTenderInput, OriginSite, Tender } from '../../types';
import { formatDate } from '../../utils';

const LANGUAGES: [string, string][] = [
  ['eng', 'English'], ['deu', 'German'], ['fra', 'French'], ['nld', 'Dutch'], ['spa', 'Spanish'],
  ['ita', 'Italian'], ['por', 'Portuguese'], ['pol', 'Polish'], ['swe', 'Swedish'], ['dan', 'Danish'],
  ['fin', 'Finnish'], ['nor', 'Norwegian'], ['ces', 'Czech'], ['slk', 'Slovak'], ['ron', 'Romanian'],
  ['hun', 'Hungarian'], ['ell', 'Greek'], ['bul', 'Bulgarian'], ['hrv', 'Croatian'], ['slv', 'Slovenian'],
  ['est', 'Estonian'], ['lav', 'Latvian'], ['lit', 'Lithuanian'], ['ara', 'Arabic'], ['msa', 'Malay'],
];
const CURRENCIES = ['EUR', 'USD', 'GBP', 'CHF', 'SEK', 'DKK', 'NOK', 'PLN', 'CZK', 'HUF', 'RON',
  'BGN', 'CAD', 'AUD', 'NZD', 'JPY', 'SGD', 'MYR', 'PHP', 'IDR', 'THB', 'INR', 'ZAR', 'TRY'];
// The same report sections a scraped tender is mapped to (normalize.map_category).
const CATEGORIES = ['Supply', 'Services', 'Works', 'Training', 'Other'];
const MAX_FILES = 10;

type Form = Omit<ManualTenderInput, 'origin_site_id' | 'cpv_codes'> & { origin_site_id: string; cpv_text: string };
type FormKey = keyof Form;

const EMPTY: Form = {
  origin_site_id: '', url: '', tag_line: '', description: '', buyer: '', country: '', place: '',
  category: '', procedure: '', pub_date: '', deadline: '', cpv_text: '', value: '', value_currency: 'EUR',
  language: 'eng',
};

// API field name -> form key (only cpv_codes differs)
const formKeyOf = (field: string): FormKey => (field === 'cpv_codes' ? 'cpv_text' : field) as FormKey;

function asFormValue(field: string, v: ExtractedField['value']): string {
  if (Array.isArray(v)) return v.join(', ');
  if (field === 'origin_site_id') return String(v);
  return String(v ?? '');
}

function fromTender(t: Tender): Form {
  return {
    origin_site_id: t.origin_site_id ? String(t.origin_site_id) : '', url: t.url ?? '',
    tag_line: t.tag_line ?? '', description: t.description ?? '', buyer: t.buyer ?? '',
    country: t.country ?? '', place: t.place ?? '', category: t.category ?? '',
    procedure: t.procedure ?? '', pub_date: (t.pub_date ?? '').slice(0, 10),
    deadline: (t.deadline ?? '').slice(0, 10), cpv_text: (t.cpv_codes ?? []).join(', '),
    value: t.value ?? '', value_currency: t.value_currency || 'EUR', language: t.language || 'eng',
  };
}

function fromDraft(d: ManualDraft): Form {
  const f: Form = { ...EMPTY };
  for (const [field, e] of Object.entries(d.prefill)) {
    const k = formKeyOf(field);
    if (k in f) (f as Record<string, string>)[k] = asFormValue(field, e.value);
  }
  return f;
}

// The request body is built from an explicit field list, never by spreading
// form state: the API refuses unknown fields (ManualTenderBody, extra="forbid"),
// and a spread once sent the form-only `cpv_text` along, which broke create,
// edit and confirm. tests/test_66_manual_payload.py checks these keys against
// the API model.
function toInput(f: Form): ManualTenderInput {
  return {
    origin_site_id: Number(f.origin_site_id),
    url: f.url,
    tag_line: f.tag_line,
    description: f.description,
    buyer: f.buyer,
    country: f.country,
    place: f.place,
    category: f.category,
    procedure: f.procedure,
    pub_date: f.pub_date,
    deadline: f.deadline,
    cpv_codes: f.cpv_text.split(/[\s,;]+/).map(s => s.trim()).filter(Boolean),
    value: f.value,
    value_currency: f.value.trim() ? f.value_currency : '',
    language: f.language,
  };
}

const labelStyle: React.CSSProperties = { display: 'block', fontSize: 12, color: '#8892a4', marginBottom: 4 };
const errStyle: React.CSSProperties = { color: '#f87171', fontSize: 12, marginTop: 4 };
const noteStyle: React.CSSProperties = { color: '#8892a4', fontSize: 12, marginTop: 4 };

export default function ManualEntry() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const editPub = params.get('edit');
  const draftId = params.get('draft') ? Number(params.get('draft')) : null;

  const [form, setForm] = useState<Form>(EMPTY);
  const [sites, setSites] = useState<OriginSite[]>([]);
  const [draft, setDraft] = useState<ManualDraft | null>(null);
  const [openDrafts, setOpenDrafts] = useState<ManualDraft[]>([]);
  const [mode, setMode] = useState<'type' | 'documents'>('type');
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<{ field?: string; message: string } | null>(null);
  const [addingSite, setAddingSite] = useState(false);
  const [newSite, setNewSite] = useState({ name: '', domain: '' });
  const [files, setFiles] = useState<File[]>([]);
  const [uploadNote, setUploadNote] = useState<string | null>(null);
  const [touched, setTouched] = useState<Set<FormKey>>(new Set());
  const fileInput = useRef<HTMLInputElement>(null);

  // initial load per screen
  useEffect(() => {
    setLoading(true);
    setLoadError(null);
    setError(null);
    setTouched(new Set());
    setDraft(null);
    Promise.all([
      getOriginSites(),
      editPub ? getTender(editPub) : Promise.resolve(null),
      draftId ? getManualDraft(draftId) : Promise.resolve(null),
      !editPub && !draftId ? listManualDrafts() : Promise.resolve([] as ManualDraft[]),
    ])
      .then(([s, t, d, open]) => {
        setSites(s);
        setOpenDrafts(open);
        if (t) {
          if (t.entry_method !== 'manual') throw new Error('Only manually entered tenders can be edited.');
          setForm(fromTender(t));
        } else if (d) {
          setDraft(d);
          setForm(fromDraft(d));
        } else {
          setForm(EMPTY);
        }
      })
      .catch(e => setLoadError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, [editPub, draftId]);

  // while the documents are being read, poll; fill the form once when ready
  useEffect(() => {
    if (!draft || draft.status !== 'extracting') return;
    const t = setTimeout(() => {
      getManualDraft(draft.id).then(d => {
        setDraft(d);
        if (d.status !== 'extracting') setForm(fromDraft(d));
      }).catch(() => {});
    }, 2000);
    return () => clearTimeout(t);
  }, [draft]);

  function set<K extends FormKey>(k: K, v: Form[K]) {
    setForm(f => ({ ...f, [k]: v }));
    setTouched(prev => new Set(prev).add(k));
    if (error?.field === k || (k === 'cpv_text' && error?.field === 'cpv_codes')) setError(null);
  }

  async function saveSite(name: string, domain: string) {
    if (!name.trim()) return;
    try {
      const s = await addOriginSite(name, domain);
      setSites(prev => (prev.some(p => p.id === s.id) ? prev : [...prev, s].sort((a, b) => a.name.localeCompare(b.name))));
      set('origin_site_id', String(s.id));
      setAddingSite(false);
      setNewSite({ name: '', domain: '' });
    } catch (e) {
      setError({ field: 'origin_site_id', message: e instanceof Error ? e.message : String(e) });
    }
  }

  function pickFiles(list: FileList | null) {
    if (!list) return;
    const pdfs = Array.from(list).filter(f => f.type === 'application/pdf' || f.name.toLowerCase().endsWith('.pdf'));
    const skipped = list.length - pdfs.length;
    setFiles(prev => [...prev, ...pdfs].slice(0, MAX_FILES));
    setUploadNote(skipped ? `${skipped} file(s) skipped: only PDFs can be read.` : null);
  }

  async function readDocuments() {
    if (!files.length || saving) return;
    setSaving(true);
    setError(null);
    try {
      const d = await createManualDraft();
      for (let i = 0; i < files.length; i++) {
        setUploadNote(`Uploading ${i + 1} of ${files.length}: ${files[i].name}`);
        await addManualDraftFile(d.id, files[i]);
      }
      setUploadNote('Starting to read…');
      await startManualDraftExtraction(d.id);
      setFiles([]);
      setUploadNote(null);
      navigate(`/scout/add-tender?draft=${d.id}`);
    } catch (err) {
      setError({ message: err instanceof Error ? err.message : String(err) });
    } finally {
      setSaving(false);
    }
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (saving) return;
    setSaving(true);
    setError(null);
    try {
      const input = toInput(form);
      const t = editPub
        ? (await updateManualTender(editPub, input)).tender
        : draft
          ? await confirmManualDraft(draft.id, input)
          : await createManualTender(input);
      navigate(`/scout/review-queue?pub=${encodeURIComponent(t.pub_number)}`);
    } catch (err) {
      if (err instanceof ApiError) setError({ field: err.field ?? undefined, message: err.message });
      else setError({ message: err instanceof Error ? err.message : String(err) });
    } finally {
      setSaving(false);
    }
  }

  async function discard() {
    if (!draft) return;
    try {
      await discardManualDraft(draft.id);
      navigate('/scout/add-tender');
    } catch (err) {
      setError({ message: err instanceof Error ? err.message : String(err) });
    }
  }

  // ── per-field markers for a draft ──────────────────────────────────────────
  function fromDocMarker(field: string) {
    const k = formKeyOf(field);
    const e = draft?.prefill[field];
    if (!e || touched.has(k)) return null;
    return (
      <span title={e.source ? `“${e.source}”` : undefined}
            style={{ marginLeft: 6, fontSize: 11, color: '#2EE6D4' }}>
        from document{e.source ? ' ⓘ' : ''}
      </span>
    );
  }

  function suggestion(field: string) {
    const e = draft?.suggestions[field];
    const k = formKeyOf(field);
    if (!e || touched.has(k)) return null;
    const shown = field === 'origin_site_id' ? (e.site?.name ?? String(e.value)) : asFormValue(field, e.value);
    return (
      <div style={{ marginTop: 4, display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
        <span className="pill pill-amber" title={e.source ? `“${e.source}”` : undefined}>
          Suggested, low confidence: {shown}
        </span>
        <button type="button" className="btn btn-ghost" style={{ fontSize: 11, padding: '2px 8px' }}
                onClick={() => set(k, asFormValue(field, e.value))}>
          Use
        </button>
      </div>
    );
  }

  const fieldError = (f: string) => (error?.field === f ? <div style={errStyle}>{error.message}</div> : null);

  function text(k: FormKey, label: string, opts: { placeholder?: string; type?: string; required?: boolean } = {}) {
    const apiField = k === 'cpv_text' ? 'cpv_codes' : k;
    return (
      <div>
        <label style={labelStyle} htmlFor={`mt-${k}`}>{label}{opts.required && ' *'}{fromDocMarker(apiField)}</label>
        <input id={`mt-${k}`} className="input-field" type={opts.type ?? 'text'} value={form[k]}
               placeholder={opts.placeholder} required={opts.required}
               onChange={e => set(k, e.target.value)} style={{ width: '100%' }} />
        {suggestion(apiField)}
        {fieldError(apiField)}
      </div>
    );
  }

  function select(k: FormKey, label: string, options: [string, string][]) {
    return (
      <div>
        <label style={labelStyle} htmlFor={`mt-${k}`}>{label}{fromDocMarker(k)}</label>
        <select id={`mt-${k}`} className="input-field" value={form[k]}
                onChange={e => set(k, e.target.value)} style={{ width: '100%' }}>
          {options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        {suggestion(k)}
        {fieldError(k)}
      </div>
    );
  }

  if (loading) return <div className="loading">Loading…</div>;
  if (loadError) return <div className="error">{loadError}</div>;

  const showForm = editPub || (draft && draft.status !== 'extracting') || (!draft && mode === 'type');
  const title = editPub ? `Edit ${editPub}` : draft ? 'Check what was read' : 'Add tender';

  return (
    <div style={{ maxWidth: 860 }}>
      <h1 style={{ fontSize: 28, fontWeight: 700, marginBottom: 4 }}>{title}</h1>
      <p style={{ color: '#8892a4', marginBottom: 20 }}>
        {editPub
          ? 'Changes are recorded in the tender’s history. Its number never changes.'
          : draft
            ? 'Nothing is saved until you add the tender. Fields marked “from document” were read with high confidence; amber suggestions need your judgement.'
            : 'For tenders from portals the Scout cannot scrape. The number, matching, value conversion and translation are filled in automatically.'}
      </p>

      {/* mode switch + unfinished drafts (new entries only) */}
      {!editPub && !draft && (
        <>
          <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
            <button type="button" className={`btn ${mode === 'type' ? 'btn-teal' : 'btn-ghost'}`} onClick={() => setMode('type')}>Type it</button>
            <button type="button" className={`btn ${mode === 'documents' ? 'btn-teal' : 'btn-ghost'}`} onClick={() => setMode('documents')}>From documents (PDF)</button>
          </div>
          {openDrafts.length > 0 && (
            <div className="card" style={{ padding: 12, marginBottom: 16 }}>
              <div style={{ fontSize: 12, color: '#8892a4', marginBottom: 6 }}>Unfinished drafts</div>
              {openDrafts.map(d => (
                <div key={d.id} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, padding: '4px 0' }}>
                  <span>{d.files.map(f => f.filename).join(', ') || 'No files'}</span>
                  <span className="pill pill-grey">{d.status}</span>
                  <span style={{ color: '#8892a4', fontSize: 12 }}>{formatDate(d.created_at)} · {d.created_by}</span>
                  <button type="button" className="btn btn-ghost" style={{ marginLeft: 'auto', fontSize: 12, padding: '2px 10px' }}
                          onClick={() => navigate(`/scout/add-tender?draft=${d.id}`)}>Open</button>
                </div>
              ))}
            </div>
          )}
        </>
      )}

      {/* upload */}
      {!editPub && !draft && mode === 'documents' && (
        <div className="card" style={{ padding: 20 }}>
          <div onDragOver={e => e.preventDefault()}
               onDrop={e => { e.preventDefault(); pickFiles(e.dataTransfer.files); }}
               onClick={() => fileInput.current?.click()}
               style={{ border: '1px dashed #2a3650', borderRadius: 8, padding: 28, textAlign: 'center', cursor: 'pointer', color: '#8892a4' }}>
            Drop the notice and tender documents here, or click to choose (PDF, up to {MAX_FILES} files)
            <input ref={fileInput} type="file" accept="application/pdf,.pdf" multiple hidden
                   onChange={e => { pickFiles(e.target.files); e.target.value = ''; }} />
          </div>
          {files.length > 0 && (
            <ul style={{ margin: '12px 0', paddingLeft: 18, fontSize: 13 }}>
              {files.map((f, i) => (
                <li key={`${f.name}-${i}`}>
                  {f.name} <span style={{ color: '#8892a4' }}>({Math.ceil(f.size / 1024)} KB)</span>{' '}
                  <button type="button" className="btn btn-ghost" style={{ fontSize: 11, padding: '0 6px' }}
                          onClick={() => setFiles(prev => prev.filter((_, j) => j !== i))}>remove</button>
                </li>
              ))}
            </ul>
          )}
          {uploadNote && <div style={noteStyle}>{uploadNote}</div>}
          {error && !error.field && <div style={errStyle}>{error.message}</div>}
          <button type="button" className="btn btn-teal" style={{ marginTop: 12 }} disabled={!files.length || saving} onClick={readDocuments}>
            {saving ? 'Uploading…' : `Read ${files.length || ''} document${files.length === 1 ? '' : 's'}`}
          </button>
        </div>
      )}

      {/* reading in progress */}
      {draft && draft.status === 'extracting' && (
        <div className="card" style={{ padding: 20 }}>
          <div className="loading" style={{ padding: 0 }}>Reading {draft.files.length} document{draft.files.length === 1 ? '' : 's'}…</div>
          <div style={noteStyle}>Large packs take a minute or two. You can leave this page; the draft stays under “Unfinished drafts”.</div>
        </div>
      )}

      {/* draft status banners */}
      {draft && draft.status === 'failed' && (
        <div className="card" style={{ padding: 12, marginBottom: 16, borderColor: 'rgba(248,113,113,0.4)' }}>
          <div style={{ color: '#f87171', fontSize: 13 }}>{draft.error || 'The documents could not be read.'}</div>
          <div style={noteStyle}>You can still type the details below; the files will be attached to the tender.</div>
        </div>
      )}
      {draft && draft.status === 'ready' && draft.files.some(f => f.skipped_pages) && (
        <div style={{ ...noteStyle, marginBottom: 12 }}>
          {draft.files.reduce((n, f) => n + (f.skipped_pages ?? 0), 0)} page(s) were not read because the draft
          reached its limit (200 pages per draft). Check those pages for anything missing:{' '}
          {draft.files.filter(f => f.skipped_pages).map(f => `${f.filename} (${f.skipped_pages})`).join(', ')}.
        </div>
      )}

      {showForm && (
        <form onSubmit={submit} className="card" style={{ padding: 20, display: 'grid', gap: 16 }}>
          {/* Where it was found */}
          <div>
            <label style={labelStyle} htmlFor="mt-origin">Found on *{fromDocMarker('origin_site_id')}</label>
            {!addingSite ? (
              <div style={{ display: 'flex', gap: 8 }}>
                <select id="mt-origin" className="input-field" value={form.origin_site_id} required
                        onChange={e => set('origin_site_id', e.target.value)} style={{ flex: 1 }}>
                  <option value="">Choose a site…</option>
                  {sites.map(s => <option key={s.id} value={s.id}>{s.name}{s.domain ? ` (${s.domain})` : ''}</option>)}
                </select>
                <button type="button" className="btn btn-ghost" onClick={() => setAddingSite(true)}>+ New site</button>
              </div>
            ) : (
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                <input className="input-field" placeholder="Site name, e.g. DTVP" value={newSite.name}
                       onChange={e => setNewSite(s => ({ ...s, name: e.target.value }))} style={{ flex: 2, minWidth: 180 }} />
                <input className="input-field" placeholder="Domain (optional), e.g. dtvp.de" value={newSite.domain}
                       onChange={e => setNewSite(s => ({ ...s, domain: e.target.value }))} style={{ flex: 1, minWidth: 140 }} />
                <button type="button" className="btn btn-teal" onClick={() => saveSite(newSite.name, newSite.domain)} disabled={!newSite.name.trim()}>Add site</button>
                <button type="button" className="btn btn-ghost" onClick={() => setAddingSite(false)}>Cancel</button>
              </div>
            )}
            {suggestion('origin_site_id')}
            {draft?.new_origin_site && !form.origin_site_id && (
              <div style={{ marginTop: 4, display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                <span className="pill pill-blue" title={draft.new_origin_site.source ? `“${draft.new_origin_site.source}”` : undefined}>
                  The document names: {draft.new_origin_site.name}{draft.new_origin_site.domain ? ` (${draft.new_origin_site.domain})` : ''}
                </span>
                <button type="button" className="btn btn-ghost" style={{ fontSize: 11, padding: '2px 8px' }}
                        onClick={() => saveSite(draft.new_origin_site!.name, draft.new_origin_site!.domain)}>
                  Add as new site
                </button>
              </div>
            )}
            {sites.length === 0 && !draft?.new_origin_site && (
              <div style={noteStyle}>No sites yet: add the first one. Existing names are matched, so “dtvp” reuses “DTVP”.</div>
            )}
            {fieldError('origin_site_id')}
          </div>

          {text('url', 'Link to the notice or documents', { placeholder: 'https://…', type: 'url' })}
          {text('tag_line', 'Title', { required: true })}
          <div>
            <label style={labelStyle} htmlFor="mt-description">Description{fromDocMarker('description')}</label>
            <textarea id="mt-description" className="input-field" rows={5} value={form.description}
                      onChange={e => set('description', e.target.value)} style={{ width: '100%', resize: 'vertical' }} />
            {suggestion('description')}
            {fieldError('description')}
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 16 }}>
            {text('buyer', 'Buyer')}
            {text('country', 'Country', { placeholder: 'e.g. DEU' })}
            {text('place', 'Place of performance')}
            {select('category', 'Category', [['', '—'], ...CATEGORIES.map(c => [c, c] as [string, string])])}
            {text('procedure', 'Procedure', { placeholder: 'e.g. open' })}
            {select('language', 'Language of the notice', LANGUAGES)}
            {text('pub_date', 'Published', { type: 'date' })}
            {text('deadline', 'Deadline', { type: 'date' })}
          </div>

          {text('cpv_text', 'CPV codes', { placeholder: 'e.g. 39522530, 35113400' })}

          <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 16 }}>
            {text('value', 'Estimated value', { placeholder: 'e.g. 250000' })}
            {select('value_currency', 'Currency', CURRENCIES.map(c => [c, c] as [string, string]))}
          </div>

          {draft && draft.files.length > 0 && (
            <div style={noteStyle}>
              Will be attached to the tender: {draft.files.map(f => f.filename).join(', ')}
            </div>
          )}

          {error && !error.field && <div style={errStyle}>{error.message}</div>}
          {error?.field && <div style={errStyle}>Please fix the highlighted field.</div>}

          <div style={{ display: 'flex', gap: 8 }}>
            <button type="submit" className="btn btn-teal" disabled={saving}>
              {saving ? 'Saving…' : editPub ? 'Save changes' : 'Add tender'}
            </button>
            {draft ? (
              <button type="button" className="btn btn-red" onClick={discard}>Discard draft</button>
            ) : (
              <button type="button" className="btn btn-ghost" onClick={() => navigate(editPub
                ? `/scout/review-queue?pub=${encodeURIComponent(editPub)}` : '/scout/review-queue')}>
                Cancel
              </button>
            )}
          </div>
        </form>
      )}
    </div>
  );
}
