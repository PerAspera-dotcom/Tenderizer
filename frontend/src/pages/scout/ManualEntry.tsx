// CR-013 §4 — manual tender entry (typed). Also the edit screen for an
// existing manual tender (?edit=MAN-2026-0001). The PDF path (Stage 4) will
// land on this same form, pre-filled, before anything is written.
//
// The user supplies only the fields below. pub_number, source, created_by and
// every derived field (matching, EUR value, exclusion, translation) are set
// by the server through the same code path as a scraped tender.
import { useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from '../../router';
import { addOriginSite, ApiError, createManualTender, getOriginSites, getTender, updateManualTender } from '../../api';
import type { ManualTenderInput, OriginSite, Tender } from '../../types';

const LANGUAGES: [string, string][] = [
  ['eng', 'English'], ['deu', 'German'], ['fra', 'French'], ['nld', 'Dutch'], ['spa', 'Spanish'],
  ['ita', 'Italian'], ['por', 'Portuguese'], ['pol', 'Polish'], ['swe', 'Swedish'], ['dan', 'Danish'],
  ['fin', 'Finnish'], ['nor', 'Norwegian'], ['ces', 'Czech'], ['slk', 'Slovak'], ['ron', 'Romanian'],
  ['hun', 'Hungarian'], ['ell', 'Greek'], ['bul', 'Bulgarian'], ['hrv', 'Croatian'], ['slv', 'Slovenian'],
  ['est', 'Estonian'], ['lav', 'Latvian'], ['lit', 'Lithuanian'], ['ara', 'Arabic'], ['msa', 'Malay'],
];
const CURRENCIES = ['EUR', 'USD', 'GBP', 'CHF', 'SEK', 'DKK', 'NOK', 'PLN', 'CZK', 'HUF', 'RON',
  'BGN', 'CAD', 'AUD', 'NZD', 'JPY', 'SGD', 'MYR', 'PHP', 'IDR', 'THB', 'INR', 'ZAR', 'TRY'];

type Form = Omit<ManualTenderInput, 'origin_site_id' | 'cpv_codes'> & { origin_site_id: string; cpv_text: string };

const EMPTY: Form = {
  origin_site_id: '', url: '', tag_line: '', description: '', buyer: '', country: '', place: '',
  category: '', procedure: '', pub_date: '', deadline: '', cpv_text: '', value: '', value_currency: 'EUR',
  language: 'eng',
};

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

function toInput(f: Form): ManualTenderInput {
  return {
    ...f,
    origin_site_id: Number(f.origin_site_id),
    cpv_codes: f.cpv_text.split(/[\s,;]+/).map(s => s.trim()).filter(Boolean),
    value_currency: f.value.trim() ? f.value_currency : '',
  };
}

const labelStyle: React.CSSProperties = { display: 'block', fontSize: 12, color: '#8892a4', marginBottom: 4 };
const errStyle: React.CSSProperties = { color: '#f87171', fontSize: 12, marginTop: 4 };

export default function ManualEntry() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const editPub = params.get('edit');
  const [form, setForm] = useState<Form>(EMPTY);
  const [sites, setSites] = useState<OriginSite[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<{ field?: string; message: string } | null>(null);
  const [addingSite, setAddingSite] = useState(false);
  const [newSite, setNewSite] = useState({ name: '', domain: '' });

  useEffect(() => {
    setLoading(true);
    setError(null);
    Promise.all([getOriginSites(), editPub ? getTender(editPub) : Promise.resolve(null)])
      .then(([s, t]) => {
        setSites(s);
        if (t) {
          if (t.entry_method !== 'manual') throw new Error('Only manually entered tenders can be edited.');
          setForm(fromTender(t));
        } else {
          setForm(EMPTY);
        }
        if (s.length === 0) setAddingSite(true);
      })
      .catch(e => setLoadError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, [editPub]);

  function set<K extends keyof Form>(k: K, v: Form[K]) {
    setForm(f => ({ ...f, [k]: v }));
    if (error?.field === k || (k === 'cpv_text' && error?.field === 'cpv_codes')) setError(null);
  }

  async function saveSite() {
    if (!newSite.name.trim()) return;
    try {
      const s = await addOriginSite(newSite.name, newSite.domain);
      setSites(prev => (prev.some(p => p.id === s.id) ? prev : [...prev, s].sort((a, b) => a.name.localeCompare(b.name))));
      set('origin_site_id', String(s.id));
      setAddingSite(false);
      setNewSite({ name: '', domain: '' });
    } catch (e) {
      setError({ field: 'origin_site_id', message: e instanceof Error ? e.message : String(e) });
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
        : await createManualTender(input);
      navigate(`/scout/review-queue?pub=${encodeURIComponent(t.pub_number)}`);
    } catch (err) {
      if (err instanceof ApiError) setError({ field: err.field, message: err.message });
      else setError({ message: err instanceof Error ? err.message : String(err) });
    } finally {
      setSaving(false);
    }
  }

  const fieldError = (f: string) => (error?.field === f ? <div style={errStyle}>{error.message}</div> : null);

  function text(k: keyof Form, label: string, opts: { placeholder?: string; type?: string; required?: boolean; list?: string } = {}) {
    return (
      <div>
        <label style={labelStyle} htmlFor={`mt-${k}`}>{label}{opts.required && ' *'}</label>
        <input id={`mt-${k}`} className="input-field" type={opts.type ?? 'text'} value={form[k]}
               placeholder={opts.placeholder} list={opts.list} required={opts.required}
               onChange={e => set(k, e.target.value)} style={{ width: '100%' }} />
        {fieldError(k === 'cpv_text' ? 'cpv_codes' : k)}
      </div>
    );
  }

  if (loading) return <div className="loading">Loading…</div>;
  if (loadError) return <div className="error">{loadError}</div>;

  return (
    <div style={{ maxWidth: 860 }}>
      <h1 style={{ fontSize: 28, fontWeight: 700, marginBottom: 4 }}>{editPub ? `Edit ${editPub}` : 'Add tender'}</h1>
      <p style={{ color: '#8892a4', marginBottom: 20 }}>
        {editPub
          ? 'Changes are recorded in the tender’s history. Its number never changes.'
          : 'For tenders from portals the Scout cannot scrape. The number, matching, value conversion and translation are filled in automatically.'}
      </p>

      <form onSubmit={submit} className="card" style={{ padding: 20, display: 'grid', gap: 16 }}>
        {/* Where it was found */}
        <div>
          <label style={labelStyle} htmlFor="mt-origin">Found on *</label>
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
              <button type="button" className="btn btn-teal" onClick={saveSite} disabled={!newSite.name.trim()}>Add site</button>
              {sites.length > 0 && <button type="button" className="btn btn-ghost" onClick={() => setAddingSite(false)}>Cancel</button>}
            </div>
          )}
          {sites.length === 0 && !error && (
            <div style={{ color: '#8892a4', fontSize: 12, marginTop: 4 }}>No sites yet: add the first one. Existing names are matched, so “dtvp” reuses “DTVP”.</div>
          )}
          {fieldError('origin_site_id')}
        </div>

        {text('url', 'Link to the notice or documents', { placeholder: 'https://…', type: 'url' })}
        {text('tag_line', 'Title', { required: true })}
        <div>
          <label style={labelStyle} htmlFor="mt-description">Description</label>
          <textarea id="mt-description" className="input-field" rows={5} value={form.description}
                    onChange={e => set('description', e.target.value)} style={{ width: '100%', resize: 'vertical' }} />
          {fieldError('description')}
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 16 }}>
          {text('buyer', 'Buyer')}
          {text('country', 'Country', { placeholder: 'e.g. DEU' })}
          {text('place', 'Place of performance')}
          {text('category', 'Category', { list: 'mt-categories', placeholder: 'Supply / Services / Works' })}
          {text('procedure', 'Procedure', { list: 'mt-procedures', placeholder: 'e.g. open' })}
          <div>
            <label style={labelStyle} htmlFor="mt-language">Language of the notice</label>
            <select id="mt-language" className="input-field" value={form.language}
                    onChange={e => set('language', e.target.value)} style={{ width: '100%' }}>
              {LANGUAGES.map(([code, name]) => <option key={code} value={code}>{name}</option>)}
            </select>
            {fieldError('language')}
          </div>
          {text('pub_date', 'Published', { type: 'date' })}
          {text('deadline', 'Deadline', { type: 'date' })}
        </div>

        {text('cpv_text', 'CPV codes', { placeholder: 'e.g. 39522530, 35113400' })}

        <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 16 }}>
          {text('value', 'Estimated value', { placeholder: 'e.g. 250000' })}
          <div>
            <label style={labelStyle} htmlFor="mt-currency">Currency</label>
            <select id="mt-currency" className="input-field" value={form.value_currency}
                    onChange={e => set('value_currency', e.target.value)} style={{ width: '100%' }}>
              {CURRENCIES.map(c => <option key={c} value={c}>{c}</option>)}
            </select>
            {fieldError('value_currency')}
          </div>
        </div>

        <datalist id="mt-categories"><option value="Supply" /><option value="Services" /><option value="Works" /></datalist>
        <datalist id="mt-procedures"><option value="open" /><option value="restricted" /><option value="negotiated" /><option value="competitive dialogue" /></datalist>

        {error && !error.field && <div style={errStyle}>{error.message}</div>}
        {error?.field && <div style={errStyle}>Please fix the highlighted field.</div>}

        <div style={{ display: 'flex', gap: 8 }}>
          <button type="submit" className="btn btn-teal" disabled={saving}>
            {saving ? 'Saving…' : editPub ? 'Save changes' : 'Add tender'}
          </button>
          <button type="button" className="btn btn-ghost" onClick={() => navigate(editPub
            ? `/scout/review-queue?pub=${encodeURIComponent(editPub)}` : '/scout/review-queue')}>
            Cancel
          </button>
        </div>
      </form>
    </div>
  );
}
