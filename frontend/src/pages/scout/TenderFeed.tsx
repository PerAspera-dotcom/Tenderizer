import { useEffect, useState, useRef } from 'react';
import { useSearchParams } from '../../router';
import { listTenders } from '../../api';
import type { Tender } from '../../types';
import { formatDate, countryFlag } from '../../utils';
import MatchChip from '../../components/MatchChip';

const PORTAL_OPTS = [
  { value: '', label: 'All portals' },
  { value: 'TED', label: 'TED' },
  { value: 'BOAMP', label: 'BOAMP' },
];

const MATCH_OPTS = [
  { value: '', label: 'All match types' },
  { value: 'both', label: 'Both' },
  { value: 'cpv', label: 'CPV' },
  { value: 'keyword', label: 'Keyword' },
];

export default function TenderFeed() {
  const [searchParams] = useSearchParams();
  const initialQ = searchParams.get('q') ?? '';

  const [tenders, setTenders] = useState<Tender[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [q, setQ] = useState(initialQ);
  const [portal, setPortal] = useState('');
  const [matchType, setMatchType] = useState('');
  const [country, setCountry] = useState('');
  const [countries, setCountries] = useState<string[]>([]);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  function load(params: { q?: string; source?: string; match_source?: string; country?: string }) {
    setLoading(true);
    listTenders({
      q: params.q || undefined,
      source: params.source || undefined,
      match_source: params.match_source || undefined,
      country: params.country || undefined,
      limit: 200,
      sort: 'deadline',
    }).then(r => {
      setTenders(r.results);
      setTotal(r.total);
      // Collect unique countries
      const cs = Array.from(new Set(r.results.map(t => t.country).filter(Boolean))).sort();
      setCountries(cs);
    }).catch(() => setError('Failed to load tenders'))
      .finally(() => setLoading(false));
  }

  useEffect(() => { load({ q: initialQ }); }, []);

  function applyFilters(newQ?: string, newPortal?: string, newMatch?: string, newCountry?: string) {
    const qVal = newQ ?? q;
    const pVal = newPortal ?? portal;
    const mVal = newMatch ?? matchType;
    const cVal = newCountry ?? country;
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      load({ q: qVal, source: pVal, match_source: mVal, country: cVal });
    }, 300);
  }

  function handleQ(val: string) { setQ(val); applyFilters(val); }
  function handlePortal(val: string) { setPortal(val); applyFilters(undefined, val); }
  function handleMatch(val: string) { setMatchType(val); applyFilters(undefined, undefined, val); }
  function handleCountry(val: string) { setCountry(val); applyFilters(undefined, undefined, undefined, val); }

  const selectStyle: React.CSSProperties = {
    background: '#151d2c', border: '1px solid #1a2334', color: '#e2e8f0',
    padding: '7px 12px', borderRadius: 6, fontSize: 13, cursor: 'pointer', outline: 'none',
  };

  return (
    <div>
      <h1 style={{ fontSize: 28, fontWeight: 700, marginBottom: 4 }}>Tender Feed</h1>
      <p style={{ color: '#8892a4', marginBottom: 20 }}>All scored matches from the latest Scout run</p>

      {/* Filter bar */}
      <div style={{ display: 'flex', gap: 10, marginBottom: 20, flexWrap: 'wrap', alignItems: 'center' }}>
        <div style={{ position: 'relative', flex: '1 1 280px', minWidth: 200 }}>
          <span style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', color: '#4c5a70', fontSize: 14 }}>⌕</span>
          <input
            className="input-field"
            style={{ paddingLeft: 30 }}
            placeholder="Filter feed…"
            value={q}
            onChange={e => handleQ(e.target.value)}
          />
        </div>
        <select style={selectStyle} value={portal} onChange={e => handlePortal(e.target.value)}>
          {PORTAL_OPTS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
        <select style={selectStyle} value={country} onChange={e => handleCountry(e.target.value)}>
          <option value="">All countries</option>
          {countries.map(c => (
            <option key={c} value={c}>{countryFlag(c)} {c}</option>
          ))}
        </select>
        <select style={selectStyle} value={matchType} onChange={e => handleMatch(e.target.value)}>
          {MATCH_OPTS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
      </div>

      {loading ? (
        <div className="loading">Loading…</div>
      ) : error ? (
        <div className="error">{error}</div>
      ) : tenders.length === 0 ? (
        <div className="loading">No tenders match the current filters.</div>
      ) : (
        <div className="card">
          <div style={{ padding: '10px 16px', borderBottom: '1px solid #1a2334', color: '#8892a4', fontSize: 12 }}>
            Showing {tenders.length} of {total} results
          </div>
          <table>
            <thead>
              <tr>
                <th style={{ width: '40%' }}>Title</th>
                <th>Portal</th>
                <th>Deadline</th>
                <th>Match</th>
                <th>Open</th>
              </tr>
            </thead>
            <tbody>
              {tenders.map(t => (
                <tr key={t.hash}>
                  <td>
                    <div style={{ fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 360, fontSize: 13 }}>
                      {t.tag_line}
                    </div>
                    <div style={{ color: '#8892a4', fontSize: 11, marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 360 }}>
                      {t.buyer}
                    </div>
                  </td>
                  <td style={{ whiteSpace: 'nowrap' }}>
                    <span style={{ fontSize: 14, marginRight: 4 }}>{countryFlag(t.country)}</span>
                    <span style={{ background: '#1a2334', color: '#e2e8f0', padding: '2px 7px', borderRadius: 4, fontSize: 11, fontWeight: 600 }}>{t.source}</span>
                  </td>
                  <td><span className="mono" style={{ fontSize: 13 }}>{formatDate(t.deadline)}</span></td>
                  <td><MatchChip matchSource={t.match_source} /></td>
                  <td>
                    {t.url ? (
                      <a href={t.url} target="_blank" rel="noopener noreferrer" className="btn btn-ghost" style={{ fontSize: 12, padding: '4px 10px' }}>Open ↗</a>
                    ) : <span style={{ color: '#4c5a70' }}>—</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
