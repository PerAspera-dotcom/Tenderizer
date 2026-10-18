import { useMemo, useState } from 'react';
import { parseNoticeText, blockSize, type NoticeBlock } from '../noticeText';

const PREVIEW_SIZE = 900;
const PREVIEW_ITEM_ROWS = 5;

const label: React.CSSProperties = { fontSize: 10, fontWeight: 700, color: '#4c5a70', textTransform: 'uppercase', letterSpacing: '0.08em' };

function Block({ b, expanded }: { b: NoticeBlock; expanded: boolean }) {
  switch (b.kind) {
    case 'heading':
      return <div style={{ fontSize: 12, fontWeight: 700, color: '#e2e8f0', margin: '14px 0 6px' }}>{b.text}</div>;
    case 'paragraph':
      return <p style={{ color: '#c8d0de', fontSize: 13, lineHeight: 1.6, margin: '0 0 10px', whiteSpace: 'pre-line' }}>{b.text}</p>;
    case 'list':
      return (
        <ul style={{ color: '#c8d0de', fontSize: 13, lineHeight: 1.6, margin: '0 0 10px', paddingLeft: 18 }}>
          {b.items.map((it, i) => <li key={i}>{it}</li>)}
        </ul>
      );
    case 'facts':
      return (
        <div style={{ margin: '0 0 10px' }}>
          {b.label && <div style={{ ...label, marginBottom: 4 }}>{b.label}</div>}
          <div style={{ display: 'grid', gridTemplateColumns: 'max-content 1fr', gap: '3px 14px', fontSize: 13 }}>
            {b.rows.map(([k, v], i) => (
              <div key={i} style={{ display: 'contents' }}>
                <span style={{ color: '#8892a4' }}>{k}</span>
                <span style={{ color: '#e2e8f0', overflowWrap: 'anywhere' }}>{v}</span>
              </div>
            ))}
          </div>
        </div>
      );
    case 'items': {
      const rows = expanded ? b.rows : b.rows.slice(0, PREVIEW_ITEM_ROWS);
      return (
        <div style={{ margin: '4px 0 12px', overflowX: 'auto' }}>
          <div style={{ ...label, marginBottom: 6 }}>{b.rows.length} line items</div>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
            <thead>
              <tr>{b.columns.map(c => (
                <th key={c} style={{ ...label, textAlign: 'left', padding: '4px 8px', borderBottom: '1px solid #1a2334', whiteSpace: 'nowrap' }}>{c}</th>
              ))}</tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i}>{r.map((cell, j) => (
                  <td key={j} className={j === 0 ? 'mono' : undefined}
                      style={{ padding: '4px 8px', borderBottom: '1px solid #131b29', color: '#c8d0de', verticalAlign: 'top' }}>{cell || '—'}</td>
                ))}</tr>
              ))}
            </tbody>
          </table>
          {!expanded && b.rows.length > PREVIEW_ITEM_ROWS && (
            <div style={{ fontSize: 11, color: '#8892a4', marginTop: 4 }}>+ {b.rows.length - PREVIEW_ITEM_ROWS} more items</div>
          )}
        </div>
      );
    }
  }
}

// Structured rendering of a notice description — headings, fact rows,
// line-item tables — collapsed to a preview when long (see noticeText.ts).
export default function NoticeText({ text }: { text: string }) {
  const [expanded, setExpanded] = useState(false);
  const blocks = useMemo(() => parseNoticeText(text), [text]);

  let shown = blocks.length;
  if (!expanded) {
    let size = 0;
    shown = blocks.findIndex(b => (size += blockSize(b)) > PREVIEW_SIZE) + 1 || blocks.length;
  }
  const truncated = shown < blocks.length || (!expanded && blocks.some(b => b.kind === 'items' && b.rows.length > PREVIEW_ITEM_ROWS));

  return (
    <div style={{ marginBottom: 20 }}>
      {blocks.slice(0, shown).map((b, i) => <Block key={i} b={b} expanded={expanded} />)}
      {(truncated || expanded) && (
        <button className="btn btn-ghost" style={{ fontSize: 12, padding: '4px 10px' }} onClick={() => setExpanded(e => !e)}>
          {expanded ? 'Show less' : 'Show full description'}
        </button>
      )}
    </div>
  );
}
