import { formatDate } from '../utils';

// Display order and labels for notice_detail keys (see
// connectors/canadabuys.notice_detail); unknown keys show after these.
const LABELS: [string, string][] = [
  ['solicitation_number', 'Solicitation no.'],
  ['notice_type', 'Notice type'],
  ['procurement_method', 'Procurement method'],
  ['selection_criteria', 'Selection criteria'],
  ['end_user', 'End user'],
  ['regions_of_opportunity', 'Open to suppliers in'],
  ['trade_agreements', 'Trade agreements'],
  ['amendment_number', 'Amendment'],
];
const CONTACT_KEYS = ['contact_name', 'contact_email', 'contact_phone'];
const HANDLED = new Set([...LABELS.map(([k]) => k), ...CONTACT_KEYS, 'contract_start', 'contract_end', 'amendment_date']);

const label: React.CSSProperties = { fontSize: 10, fontWeight: 700, color: '#4c5a70', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 3 };

function humanize(key: string) {
  return key.replace(/_/g, ' ').replace(/^./, c => c.toUpperCase());
}

// Facts the source publishes beyond the shared columns, so a reader has the
// reference, contract period and buyer contact without opening the portal.
export default function NoticeDetails({ detail }: { detail: Record<string, string> | null | undefined }) {
  if (!detail || !Object.keys(detail).length) return null;
  const rows: [string, React.ReactNode][] = [];
  for (const [key, text] of LABELS) {
    if (!detail[key]) continue;
    const value = key === 'amendment_number' && detail.amendment_date
      ? `${detail[key]} · ${formatDate(detail.amendment_date)}` : detail[key];
    rows.push([text, value]);
  }
  if (detail.contract_start || detail.contract_end) {
    rows.push(['Contract period', `${detail.contract_start ? formatDate(detail.contract_start) : '?'} – ${detail.contract_end ? formatDate(detail.contract_end) : '?'}`]);
  }
  for (const [key, value] of Object.entries(detail)) {
    if (!HANDLED.has(key) && value) rows.push([humanize(key), value]);
  }
  const hasContact = CONTACT_KEYS.some(k => detail[k]);

  return (
    <div style={{ marginBottom: 20 }}>
      <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.08em', color: '#4c5a70', textTransform: 'uppercase', marginBottom: 12 }}>Notice Details</div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px 20px' }}>
        {rows.map(([k, v]) => (
          <div key={k}>
            <div style={label}>{k}</div>
            <div style={{ fontSize: 13, overflowWrap: 'anywhere' }}>{v}</div>
          </div>
        ))}
        {hasContact && (
          <div>
            <div style={label}>Contact</div>
            <div style={{ fontSize: 13, overflowWrap: 'anywhere', lineHeight: 1.5 }}>
              {detail.contact_name && <div>{detail.contact_name}</div>}
              {detail.contact_email && <div><a href={`mailto:${detail.contact_email}`} style={{ color: '#60a5fa' }}>{detail.contact_email}</a></div>}
              {detail.contact_phone && <div><a href={`tel:${detail.contact_phone}`} style={{ color: '#60a5fa' }}>{detail.contact_phone}</a></div>}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
