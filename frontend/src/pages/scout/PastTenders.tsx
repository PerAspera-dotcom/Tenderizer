import { Fragment, useEffect, useState, useRef } from 'react';
import { listTenders } from '../../api';
import type { Tender, AwardDetail } from '../../types';
import { formatDate, countryFlag, formatValue, hasTranslatedTagLine, displayTagLine } from '../../utils';

function DetailField({ label, value }: { label: string; value?: string | null }) {
  if (!value) return null;
  return (
    <div>
      <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.06em', color: '#4c5a70', textTransform: 'uppercase', marginBottom: 2 }}>{label}</div>
      <div className="mono" style={{ fontSize: 12, color: '#c8d0de' }}>{value}</div>
    </div>
  );
}

// Past-tenders data-coverage follow-up: winner org / lot / contract detail,
// shown expanded (row click) rather than as more table columns — only a
// minority of rows have it (single-lot/single-winner notices only), and the
// full set is too much to cram into the table itself.
function AwardDetailPanel({ detail }: { detail: AwardDetail }) {
  const w = detail.winner ?? {};
  const lot = detail.lot ?? {};
  const contract = detail.contract ?? {};
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '12px 20px', padding: '14px 20px' }}>
      <DetailField label="Registration no." value={w.registration_number} />
      <DetailField label="Winner city" value={w.city} />
      <DetailField label="Postal code" value={w.postal_code} />
      <DetailField label="NUTS / country" value={[w.nuts, w.country].filter(Boolean).join(' · ') || undefined} />
      <DetailField label="Company size" value={w.size} />
      <DetailField
        label="Regulated market"
        value={w.regulated_market === undefined ? undefined : (w.regulated_market ? 'Listed' : 'Not listed')}
      />
      <DetailField label="Decision date" value={w.decision_date} />
      <DetailField label="Lot" value={lot.identifier} />
      <DetailField label="Lot title" value={lot.title} />
      <DetailField label="Duration" value={lot.duration} />
      <DetailField label="Contract ref." value={contract.identifier} />
      <DetailField label="Contract concluded" value={contract.conclusion_date} />
      <DetailField label="Tender ref." value={contract.tender_identifier} />
      <DetailField label="Framework max value" value={formatValue(detail.framework_max_value, detail.framework_max_currency)} />
    </div>
  );
}

const PORTAL_OPTS = [
  { value: '', label: 'All portals' },
  { value: 'TED', label: 'TED' },
  { value: 'BOAMP', label: 'BOAMP' },
];

// CR-002 B1: past_tender notices get their own aggregated view — table,
// filters, counts, same shape as the Tender Feed — but never mixed with
// active tenders anywhere (Review Queue or Tender Feed).
export default function PastTenders() {
  const [tenders, setTenders] = useState<Tender[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [q, setQ] = useState('');
  const [portal, setPortal] = useState('');
  const [country, setCountry] = useState('');
  const [countries, setCountries] = useState<string[]>([]);
  const [expandedHash, setExpandedHash] = useState<string | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  function load(params: { q?: string; source?: string; country?: string }) {
    setLoading(true);
    listTenders({
      q: params.q || undefined,
      source: params.source || undefined,
      country: params.country || undefined,
      notice_type: 'past_tender',
      limit: 200,
    }).then(r => {
      const sorted = [...r.results].sort((a, b) => (b.pub_date || '').localeCompare(a.pub_date || ''));
      setTenders(sorted);
      setTotal(r.total);
      const cs = Array.from(new Set(r.results.map(t => t.country).filter(Boolean))).sort();
      setCountries(cs);
    }).catch(() => setError('Failed to load past tenders'))
      .finally(() => setLoading(false));
  }

  useEffect(() => { load({}); }, []);

  function applyFilters(newQ?: string, newPortal?: string, newCountry?: string) {
    const qVal = newQ ?? q;
    const pVal = newPortal ?? portal;
    const cVal = newCountry ?? country;
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      load({ q: qVal, source: pVal, country: cVal });
    }, 300);
  }

  function handleQ(val: string) { setQ(val); applyFilters(val); }
  function handlePortal(val: string) { setPortal(val); applyFilters(undefined, val); }
  function handleCountry(val: string) { setCountry(val); applyFilters(undefined, undefined, val); }

  const selectStyle: React.CSSProperties = {
    background: '#151d2c', border: '1px solid #1a2334', color: '#e2e8f0',
    padding: '7px 12px', borderRadius: 6, fontSize: 13, cursor: 'pointer', outline: 'none',
  };

  const withAward = tenders.filter(t => t.awarded_to || t.awarded_value).length;

  return (
    <div>
      <h1 style={{ fontSize: 28, fontWeight: 700, marginBottom: 4 }}>Past Tenders</h1>
      <p style={{ color: '#8892a4', marginBottom: 16 }}>Historical / awarded notices — identified by an empty deadline, not part of active triage</p>

      <div style={{ display: 'flex', gap: 10, marginBottom: 20 }}>
        <span className="pill pill-grey">{total} past tenders</span>
        <span className="pill pill-green">{withAward} with award info</span>
      </div>

      {/* Filter bar */}
      <div style={{ display: 'flex', gap: 10, marginBottom: 20, flexWrap: 'wrap', alignItems: 'center' }}>
        <div style={{ position: 'relative', flex: '1 1 280px', minWidth: 200 }}>
          <span style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', color: '#4c5a70', fontSize: 14 }}>⌕</span>
          <input
            className="input-field"
            style={{ paddingLeft: 30 }}
            placeholder="Filter past tenders…"
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
      </div>

      {loading ? (
        <div className="loading">Loading…</div>
      ) : error ? (
        <div className="error">{error}</div>
      ) : tenders.length === 0 ? (
        <div className="loading">No past tenders match the current filters.</div>
      ) : (
        <div className="card">
          <div style={{ padding: '10px 16px', borderBottom: '1px solid #1a2334', color: '#8892a4', fontSize: 12 }}>
            Showing {tenders.length} of {total} results
          </div>
          <table>
            <thead>
              <tr>
                <th style={{ width: '30%' }}>Title</th>
                <th>Portal</th>
                <th>Published</th>
                <th>Awarded To</th>
                <th>Awarded Value</th>
                <th>Open</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {tenders.map(t => {
                const isExpanded = expandedHash === t.hash;
                return (
                  <Fragment key={t.hash}>
                    <tr>
                      <td>
                        <div style={{ fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 320, fontSize: 13 }}>
                          {hasTranslatedTagLine(t) && (
                            <span title={`Translated — original: ${t.tag_line}`} style={{ marginRight: 4 }}>🌐</span>
                          )}
                          {displayTagLine(t)}
                        </div>
                        <div style={{ color: '#8892a4', fontSize: 11, marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 320 }}>
                          {t.buyer}
                        </div>
                      </td>
                      <td style={{ whiteSpace: 'nowrap' }}>
                        <span style={{ fontSize: 14, marginRight: 4 }}>{countryFlag(t.country)}</span>
                        <span style={{ background: '#1a2334', color: '#e2e8f0', padding: '2px 7px', borderRadius: 4, fontSize: 11, fontWeight: 600 }}>{t.source}</span>
                      </td>
                      <td><span className="mono" style={{ fontSize: 13 }}>{formatDate(t.pub_date)}</span></td>
                      <td style={{ fontSize: 13 }}>{t.awarded_to || <span style={{ color: '#4c5a70' }}>—</span>}</td>
                      <td className="mono" style={{ fontSize: 13 }}>
                        {formatValue(t.awarded_value, t.awarded_currency) || <span style={{ color: '#4c5a70' }}>—</span>}
                      </td>
                      <td>
                        {t.url ? (
                          <a href={t.url} target="_blank" rel="noopener noreferrer" className="btn btn-ghost" style={{ fontSize: 12, padding: '4px 10px' }}>Open ↗</a>
                        ) : <span style={{ color: '#4c5a70' }}>—</span>}
                      </td>
                      <td>
                        {t.award_detail && (
                          <button
                            className="btn btn-ghost"
                            style={{ fontSize: 12, padding: '4px 8px' }}
                            onClick={() => setExpandedHash(isExpanded ? null : t.hash)}
                          >
                            {isExpanded ? '▾' : '▸'} Detail
                          </button>
                        )}
                      </td>
                    </tr>
                    {isExpanded && t.award_detail && (
                      <tr>
                        <td colSpan={7} style={{ padding: 0, background: 'rgba(46,230,212,0.03)', borderTop: 'none' }}>
                          <AwardDetailPanel detail={t.award_detail} />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
