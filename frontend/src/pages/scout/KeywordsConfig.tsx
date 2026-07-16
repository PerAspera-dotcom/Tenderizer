import { useEffect, useMemo, useState } from 'react';
import { getKeywordsConfig, putKeywordsConfig } from '../../api';
import type { KeywordsConfig } from '../../types';

const LANGUAGES = ['en', 'fr', 'nl', 'de'] as const;
const LANG_LABEL: Record<string, string> = { en: 'EN', fr: 'FR', nl: 'NL', de: 'DE' };

interface Row {
  lang: string;
  term: string;
}

export default function KeywordsConfigPage() {
  const [data, setData] = useState<KeywordsConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [newLang, setNewLang] = useState<string>('en');
  const [newTerm, setNewTerm] = useState('');
  const [adding, setAdding] = useState(false);
  const [addError, setAddError] = useState('');
  const [busyKey, setBusyKey] = useState<string | null>(null);

  useEffect(() => {
    getKeywordsConfig()
      .then(setData)
      .catch(() => setError('Failed to load keyword configuration'))
      .finally(() => setLoading(false));
  }, []);

  const rows: Row[] = useMemo(() => {
    if (!data) return [];
    return LANGUAGES.flatMap(lang => (data.terms[lang] || []).map(term => ({ lang, term })));
  }, [data]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return rows;
    return rows.filter(r => r.term.toLowerCase().includes(q) || r.lang.includes(q));
  }, [rows, search]);

  const distinctiveSet = useMemo(() => new Set(data?.distinctive ?? []), [data]);

  // CR-003 G2: bulk enable/disable a whole language's terms as distinctive in
  // one action, instead of toggling each of up to ~47 terms individually.
  // Reuses the same save path as the per-term toggle below — no new storage
  // shape, "distinctive" stays a single flat array (client-side language
  // grouping just computes the next full array to send).
  async function toggleLanguageDistinctive(lang: string) {
    if (!data) return;
    const langTerms = data.terms[lang] || [];
    if (langTerms.length === 0) return;
    const allDistinctive = langTerms.every(t => distinctiveSet.has(t));
    const busyLangKey = `lang:${lang}`;
    setBusyKey(busyLangKey);
    try {
      const next = allDistinctive
        ? data.distinctive.filter(t => !langTerms.includes(t))
        : Array.from(new Set([...data.distinctive, ...langTerms]));
      await putKeywordsConfig({ distinctive: next });
      setData({ ...data, distinctive: next });
    } catch {
      setError('Failed to save — this change was not applied');
    } finally {
      setBusyKey(null);
    }
  }

  async function toggleDistinctive(term: string) {
    if (!data) return;
    setBusyKey(term);
    try {
      const next = distinctiveSet.has(term)
        ? data.distinctive.filter(t => t !== term)
        : [...data.distinctive, term];
      await putKeywordsConfig({ distinctive: next });
      setData({ ...data, distinctive: next });
    } catch {
      setError('Failed to save — this change was not applied');
    } finally {
      setBusyKey(null);
    }
  }

  async function removeTerm(lang: string, term: string) {
    if (!data) return;
    const key = `${lang}:${term}`;
    setBusyKey(key);
    try {
      const nextTerms = { ...data.terms, [lang]: (data.terms[lang] || []).filter(t => t !== term) };
      // A removed term can't stay distinctive — avoids an orphaned distinctive
      // entry with no backing term (same spirit as CR-001 F8's term/code
      // consistency check, just applied client-side on removal).
      const nextDistinctive = data.distinctive.filter(t => t !== term);
      await putKeywordsConfig({ terms: nextTerms, distinctive: nextDistinctive });
      setData({ terms: nextTerms, distinctive: nextDistinctive });
    } catch {
      setError('Failed to save — this term was not removed');
    } finally {
      setBusyKey(null);
    }
  }

  async function addTerm(e: React.FormEvent) {
    e.preventDefault();
    if (!data) return;
    const term = newTerm.trim();
    if (!term) return;
    setAddError('');
    if ((data.terms[newLang] || []).includes(term)) {
      setAddError(`"${term}" is already in the ${LANG_LABEL[newLang]} list`);
      return;
    }
    setAdding(true);
    try {
      const nextTerms = { ...data.terms, [newLang]: [...(data.terms[newLang] || []), term] };
      await putKeywordsConfig({ terms: nextTerms });
      setData({ ...data, terms: nextTerms });
      setNewTerm('');
    } catch {
      setAddError('Failed to reach the server — try again');
    } finally {
      setAdding(false);
    }
  }

  if (loading) return <div className="loading">Loading…</div>;
  if (error) return <div className="error">{error}</div>;
  if (!data) return null;

  return (
    <div>
      <h1 style={{ fontSize: 28, fontWeight: 700, marginBottom: 4 }}>Keywords</h1>
      <p style={{ color: '#8892a4', marginBottom: 20 }}>
        Multilingual keyword safeguard library — terms marked <strong>distinctive</strong> feed the live TED title query; edit with care
      </p>

      {/* Add term */}
      <form onSubmit={addTerm} style={{ display: 'flex', gap: 8, alignItems: 'flex-start', marginBottom: 12 }}>
        <select
          value={newLang}
          onChange={e => setNewLang(e.target.value)}
          disabled={adding}
          style={{ background: '#151d2c', border: '1px solid #1a2334', color: '#e2e8f0', padding: '7px 12px', borderRadius: 6, fontSize: 13, cursor: 'pointer', outline: 'none' }}
        >
          {LANGUAGES.map(l => <option key={l} value={l}>{LANG_LABEL[l]}</option>)}
        </select>
        <div style={{ flex: '0 1 280px' }}>
          <input
            className="input-field"
            placeholder="+ Add term, e.g. tent"
            value={newTerm}
            onChange={ev => { setNewTerm(ev.target.value); setAddError(''); }}
            disabled={adding}
          />
        </div>
        <button type="submit" className="btn btn-teal" disabled={adding || !newTerm.trim()}>
          {adding ? 'Adding…' : '+ Add term'}
        </button>
      </form>
      {addError && <div style={{ color: '#f87171', fontSize: 13, marginBottom: 12 }}>{addError}</div>}

      {/* Search */}
      <div style={{ position: 'relative', maxWidth: 360, marginBottom: 16 }}>
        <span style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', color: '#4c5a70', fontSize: 14 }}>⌕</span>
        <input
          className="input-field"
          style={{ paddingLeft: 30 }}
          placeholder="Search term or language…"
          value={search}
          onChange={e => setSearch(e.target.value)}
        />
      </div>

      {/* CR-003 G2: bulk per-language distinctive toggle */}
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 12 }}>
        {LANGUAGES.map(lang => {
          const langTerms = data.terms[lang] || [];
          const distinctiveCount = langTerms.filter(t => distinctiveSet.has(t)).length;
          const allDistinctive = langTerms.length > 0 && distinctiveCount === langTerms.length;
          const busy = busyKey === `lang:${lang}`;
          return (
            <button
              key={lang}
              className="btn btn-ghost"
              style={{ fontSize: 12, padding: '6px 12px', opacity: busy ? 0.5 : 1 }}
              onClick={() => toggleLanguageDistinctive(lang)}
              disabled={busy || langTerms.length === 0}
              title={allDistinctive
                ? `Disable all ${LANG_LABEL[lang]} terms as distinctive`
                : `Enable all ${LANG_LABEL[lang]} terms as distinctive`}
            >
              {LANG_LABEL[lang]} · {distinctiveCount}/{langTerms.length} distinctive — {allDistinctive ? 'Disable all' : 'Enable all'}
            </button>
          );
        })}
      </div>

      <div className="card">
        <div style={{ padding: '10px 16px', borderBottom: '1px solid #1a2334', color: '#8892a4', fontSize: 12 }}>
          {filtered.length} of {rows.length} terms · {data.distinctive.length} distinctive
        </div>
        {filtered.length === 0 ? (
          <div style={{ padding: 32, textAlign: 'center', color: '#8892a4' }}>No terms match your search.</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Term</th>
                <th style={{ width: 70 }}>Lang</th>
                <th>Distinctive</th>
                <th style={{ width: 40 }}></th>
              </tr>
            </thead>
            <tbody>
              {filtered.map(r => {
                const key = `${r.lang}:${r.term}`;
                const isDistinctive = distinctiveSet.has(r.term);
                const busy = busyKey === r.term || busyKey === key;
                return (
                  <tr key={key}>
                    <td style={{ fontSize: 13 }}>{r.term}</td>
                    <td>
                      <span className="mono" style={{ fontSize: 11, color: '#8892a4' }}>{LANG_LABEL[r.lang]}</span>
                    </td>
                    <td>
                      <button
                        role="switch"
                        aria-checked={isDistinctive}
                        title={isDistinctive
                          ? 'Distinctive — feeds the live TED title query. Click to demote to safeguard-only.'
                          : 'Safeguard-only. Click to promote to distinctive (feeds the live TED title query).'}
                        onClick={() => toggleDistinctive(r.term)}
                        disabled={busy}
                        style={{
                          width: 36, height: 20, borderRadius: 9999, border: 'none', position: 'relative',
                          background: isDistinctive ? '#2EE6D4' : '#1f2b40', opacity: busy ? 0.5 : 1, flexShrink: 0,
                        }}
                      >
                        <span style={{
                          position: 'absolute', top: 2, left: isDistinctive ? 18 : 2, width: 16, height: 16, borderRadius: '50%',
                          background: isDistinctive ? '#0f1623' : '#6b7990', transition: 'left 0.1s',
                        }} />
                      </button>
                    </td>
                    <td>
                      <button
                        className="btn btn-ghost"
                        style={{ fontSize: 11, padding: '3px 8px' }}
                        onClick={() => removeTerm(r.lang, r.term)}
                        disabled={busy}
                      >
                        Remove
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
