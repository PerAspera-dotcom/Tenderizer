import { useState } from 'react';
import { useNavigate } from '../../router';

// API not yet built — static preview data

const COMPLETE = 4;
const TOTAL = 9;
const TO_COMPLETE = 2;
const TO_LINK = 3;
const OUTSTANDING = TO_COMPLETE + TO_LINK;

const RUN_DATA = [
  { label: 'Run 1', count: 9 },
  { label: 'Run 2', count: 7 },
  { label: 'Run 3', count: 5 },
  { label: 'Current', count: 5 },
];

const MAX_BAR_HEIGHT = 80;
const MAX_RUN_COUNT = Math.max(...RUN_DATA.map(r => r.count));

interface GapItem {
  num: string;
  title: string;
  note: string;
  closestDoc?: string;
}

const TO_COMPLETE_ITEMS: GapItem[] = [
  {
    num: '3.6',
    title: 'Delivery within 60 days of award',
    note: 'No supporting documentation found. A delivery-schedule or logistics commitment document must be added before submission.',
  },
  {
    num: '3.8',
    title: 'Spare-parts availability — 10 years',
    note: 'No supporting documentation found. A spare-parts commitment letter from the manufacturer must be added.',
  },
];

const TO_LINK_ITEMS: GapItem[] = [
  {
    num: '4.1',
    title: 'ISO 5912 conformity',
    note: '',
    closestDoc: 'tech_ISO-5912-cert.pdf — link this document to confirm compliance.',
  },
  {
    num: '3.9',
    title: 'Wind load resistance ≥ 100 km/h',
    note: '',
    closestDoc: 'Aluminium pole tensile strength — cert.pdf — link to substantiate EN 13782 compliance.',
  },
  {
    num: '5.2',
    title: 'Packaging requirements',
    note: '',
    closestDoc: 'Tent fabric spec — 600D polyester.pdf — link to confirm weatherproof packaging spec.',
  },
];

function Donut({ done, total }: { done: number; total: number }) {
  const pct = done / total;
  const deg = Math.round(pct * 360);
  return (
    <div style={{ position: 'relative', width: 90, height: 90, flexShrink: 0 }}>
      <div style={{
        width: 90, height: 90, borderRadius: '50%',
        background: `conic-gradient(#34d399 0deg ${deg}deg, #1f2b40 ${deg}deg 360deg)`,
      }} />
      {/* Hole */}
      <div style={{
        position: 'absolute', inset: 12, borderRadius: '50%',
        background: 'rgba(227,179,65,0.07)',
        display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
      }}>
        <span className="mono" style={{ fontSize: 16, fontWeight: 700, color: '#cdd6e3', lineHeight: 1 }}>{done}/{total}</span>
        <span style={{ fontSize: 10, color: '#6b7990', marginTop: 2 }}>done</span>
      </div>
    </div>
  );
}

export default function GapsReport() {
  const navigate = useNavigate();
  const [resolved, setResolve] = useState<Set<string>>(new Set());

  function markResolved(num: string) {
    setResolve(prev => { const s = new Set(prev); s.add(num); return s; });
  }

  const visibleLinks = TO_LINK_ITEMS.filter(i => !resolved.has(i.num));

  return (
    <div>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 6, flexWrap: 'wrap' }}>
        <h1 style={{ fontSize: 28, fontWeight: 700 }}>Gaps Report</h1>
        <span style={{ background: 'rgba(192,132,252,0.15)', color: '#c084fc', border: '1px solid rgba(192,132,252,0.3)', borderRadius: 6, fontSize: 11, fontWeight: 700, padding: '3px 8px' }}>COMPOSER</span>
        <span style={{ background: 'rgba(227,179,65,0.12)', color: '#e3b341', border: '1px solid rgba(227,179,65,0.3)', borderRadius: 6, fontSize: 11, fontWeight: 700, padding: '3px 8px' }}>🚧 UNDER CONSTRUCTION</span>
      </div>
      <p style={{ color: '#8a97ac', marginBottom: 20, fontSize: 13 }}>
        The single number that matters: outstanding gaps. The proposal is not submission-ready until it reaches zero.
      </p>

      {/* Top 2-column grid */}
      <div style={{ display: 'grid', gridTemplateColumns: '1.5fr 1fr', gap: 16, marginBottom: 24 }}>
        {/* Readiness card */}
        <div style={{ background: 'rgba(227,179,65,0.07)', border: '1px solid rgba(227,179,65,0.3)', borderRadius: 13, padding: '20px 24px', display: 'flex', alignItems: 'center', gap: 24 }}>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.1em', color: '#6b7990', textTransform: 'uppercase', marginBottom: 8 }}>Submission status</div>
            <div style={{ fontSize: 36, fontWeight: 800, color: '#e3b341', lineHeight: 1, marginBottom: 10 }}>
              {OUTSTANDING} items outstanding
            </div>
            <div style={{ fontSize: 13, color: '#8a97ac' }}>
              {TO_COMPLETE} to be completed · {TO_LINK} to be linked · {COMPLETE} of {TOTAL} complete
            </div>
          </div>
          <Donut done={COMPLETE} total={TOTAL} />
        </div>

        {/* Run-progress card */}
        <div className="card" style={{ padding: '16px 18px' }}>
          <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.1em', color: '#6b7990', textTransform: 'uppercase', marginBottom: 16 }}>Gaps closed across runs</div>
          <div style={{ display: 'flex', alignItems: 'flex-end', gap: 12, height: MAX_BAR_HEIGHT + 32 }}>
            {RUN_DATA.map((run, i) => {
              const isCurrent = i === RUN_DATA.length - 1;
              const barH = Math.round((run.count / MAX_RUN_COUNT) * MAX_BAR_HEIGHT);
              return (
                <div key={run.label} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4 }}>
                  <span className="mono" style={{ fontSize: 12, fontWeight: 600, color: isCurrent ? '#e3b341' : '#cdd6e3' }}>{run.count}</span>
                  <div style={{
                    width: '100%', height: barH,
                    background: isCurrent ? '#e3b341' : '#3a4a66',
                    borderRadius: '4px 4px 0 0',
                    minHeight: 6,
                  }} />
                  <span style={{ fontSize: 11, color: '#6b7990', whiteSpace: 'nowrap' }}>{run.label}</span>
                </div>
              );
            })}
          </div>
        </div>
      </div>

      {/* To be completed */}
      <div className="card" style={{ marginBottom: 16, overflow: 'hidden' }}>
        <div style={{ padding: '12px 18px', borderBottom: '1px solid #1f2b40', display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ width: 8, height: 8, borderRadius: '50%', background: '#e3b341', display: 'inline-block' }} />
          <span style={{ fontWeight: 600, fontSize: 14 }}>To be completed</span>
          <span style={{ color: '#8a97ac', fontSize: 12, marginLeft: 4 }}>no supporting documentation found</span>
          <span className="mono" style={{ marginLeft: 'auto', fontSize: 14, fontWeight: 700, color: '#e3b341' }}>{TO_COMPLETE_ITEMS.length}</span>
        </div>
        {TO_COMPLETE_ITEMS.map(item => (
          <div key={item.num} style={{ padding: '14px 18px', borderBottom: '1px solid #1b2536', display: 'flex', gap: 14, alignItems: 'flex-start' }}>
            <span className="mono" style={{ fontSize: 13, color: '#6b7990', flexShrink: 0, width: 28 }}>{item.num}</span>
            <div style={{ flex: 1 }}>
              <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 4 }}>{item.title}</div>
              <div style={{ fontSize: 12, color: '#8a97ac' }}>{item.note}</div>
            </div>
            <button className="btn btn-amber" style={{ flexShrink: 0, fontSize: 12 }} onClick={() => navigate('/composer/ingest')}>
              + Add document
            </button>
          </div>
        ))}
      </div>

      {/* To be linked */}
      <div className="card" style={{ marginBottom: 20, overflow: 'hidden' }}>
        <div style={{ padding: '12px 18px', borderBottom: '1px solid #1f2b40', display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ width: 8, height: 8, borderRadius: '50%', background: '#60a5fa', display: 'inline-block' }} />
          <span style={{ fontWeight: 600, fontSize: 14 }}>To be linked</span>
          <span className="mono" style={{ marginLeft: 'auto', fontSize: 14, fontWeight: 700, color: '#60a5fa' }}>{visibleLinks.length}</span>
        </div>
        {visibleLinks.map(item => (
          <div key={item.num} style={{ padding: '14px 18px', borderBottom: '1px solid #1b2536', display: 'flex', gap: 14, alignItems: 'flex-start' }}>
            <span className="mono" style={{ fontSize: 13, color: '#6b7990', flexShrink: 0, width: 28 }}>{item.num}</span>
            <div style={{ flex: 1 }}>
              <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 4 }}>{item.title}</div>
              {item.closestDoc && (
                <div style={{ fontSize: 12, color: '#60a5fa' }}>Closest match: <span className="mono">{item.closestDoc}</span></div>
              )}
            </div>
            <div style={{ display: 'flex', gap: 8, flexShrink: 0 }}>
              <button className="btn btn-blue" style={{ fontSize: 12 }}>Link document</button>
              <button className="btn btn-ghost" style={{ fontSize: 12 }} onClick={() => markResolved(item.num)}>Mark resolved</button>
            </div>
          </div>
        ))}
        {visibleLinks.length === 0 && (
          <div style={{ padding: '16px 18px', color: '#34d399', fontSize: 13, display: 'flex', alignItems: 'center', gap: 8 }}>
            ✓ All links resolved
          </div>
        )}
      </div>

      {/* Footer */}
      <div>
        <button className="btn btn-ghost">⤓ Download gaps_report.txt</button>
      </div>
    </div>
  );
}
