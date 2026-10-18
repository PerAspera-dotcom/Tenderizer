// Turns a notice's free-text description into display blocks. Sources send
// very different shapes: CanadaBuys keeps line structure (headings,
// "Key: value" lines, repeated "Item N" blocks), TED sends one line with
// inline "1. … 2. …" numbering, most others plain paragraphs. Rendering it as
// one <p> collapsed all of that into a wall of text.

export type NoticeBlock =
  | { kind: 'heading'; text: string }
  | { kind: 'paragraph'; text: string }
  | { kind: 'facts'; rows: [string, string][]; label?: string }
  | { kind: 'list'; items: string[] }
  | { kind: 'items'; columns: string[]; rows: string[][] };

const KV_RE = /^([A-Za-z][A-Za-z0-9 ./&()'#-]{0,40}?)\s*:\s+(.+)$/;
const ITEM_RE = /^(?:item|line(?:\s+item)?|lot)\s*(?:no\.?\s*)?(\d+)\s*[,:.-]?\s*(.*)$/i;
const BULLET_RE = /^\s*(?:[-•*▪●◦]|o(?=\s))\s+(.*)$/;
const MAX_FACT_VALUE = 120;

function kv(line: string): [string, string] | null {
  const m = KV_RE.exec(line);
  if (!m || m[2].startsWith('//') || m[2].length > MAX_FACT_VALUE) return null;
  return [m[1].trim(), m[2].trim()];
}

function isHeading(line: string): boolean {
  const letters = line.replace(/[^A-Za-zÀ-ÿ]/g, '');
  return letters.length >= 4 && line.length <= 80 && letters === letters.toUpperCase() && !/[.;,]$/.test(line);
}

function normalize(text: string): string {
  let t = text.replace(/\r\n?/g, '\n').split('\n').map(l => l.replace(/\s+$/, '')).join('\n');
  t = t.replace(/\n{3,}/g, '\n\n').trim();
  // TED-style single line "1. … 2. …": break before each number that follows a
  // sentence end, when the numbering really runs 1, 2, …
  if (!t.includes('\n') && /(^|\s)1\.\s/.test(t) && /[.;:!?”"]\s+2\.\s/.test(t)) {
    t = t.replace(/([.;:!?”"])\s+(?=\d{1,2}\.\s)/g, '$1\n\n');
  }
  return t;
}

interface Item { n: string; desc: string; fields: Record<string, string>; notes: string[] }

function parseItem(lines: string[]): Item | null {
  const m = ITEM_RE.exec(lines[0]);
  if (!m) return null;
  const fields: Record<string, string> = {};
  const descParts: string[] = [];
  // header remainder, e.g. "GSIN: 7690, NSN: 7690-20-0039875, MARKER, IDENTIFICATION"
  for (const part of m[2].split(/,\s*/).filter(Boolean)) {
    const f = kv(part);
    if (f) fields[f[0]] = f[1];
    else descParts.push(part);
  }
  const notes: string[] = [];
  for (const line of lines.slice(1)) {
    const f = kv(line.trim());
    if (f) fields[f[0]] = f[1];
    else if (line.trim()) notes.push(line.trim());
  }
  return { n: m[1], desc: descParts.join(', '), fields, notes };
}

function itemsBlock(items: Item[]): NoticeBlock[] {
  // Fields with the same value on every item ("GSIN: 7690") go in one shared
  // facts row; the table keeps fields present on at least half the items,
  // quantity first, with unit of issue merged into the quantity cell.
  const counts = new Map<string, number>();
  for (const it of items) for (const k of Object.keys(it.fields)) counts.set(k, (counts.get(k) ?? 0) + 1);
  const shared = [...counts.keys()].filter(k => counts.get(k) === items.length
    && items.every(it => it.fields[k] === items[0].fields[k]));
  let keys = [...counts.keys()].filter(k => !shared.includes(k) && (counts.get(k) ?? 0) * 2 >= items.length);
  const qty = keys.find(k => /^(quantity|qty)$/i.test(k));
  const unit = [...counts.keys()].find(k => /^unit( of issue| of measure)?$/i.test(k));
  keys = keys.filter(k => k !== unit && k !== qty);
  keys = [...(qty ? [qty] : []), ...keys].slice(0, 4);
  const qtyCell = (it: Item) => `${it.fields[qty!] ?? ''} ${unit && !shared.includes(unit) ? it.fields[unit] ?? '' : ''}`.trim();
  const notes = items.map(it => it.notes.join(' · '));
  const sharedNote = notes.every(n => n === notes[0]) ? notes[0] : '';
  const hasDesc = items.some(it => it.desc);
  const hasNotes = !sharedNote && notes.some(Boolean);
  const qtyLabel = unit && shared.includes(unit) ? `Qty (${items[0].fields[unit]})` : 'Qty';
  const columns = ['#', ...(hasDesc ? ['Item'] : []), ...keys.map(k => (k === qty ? qtyLabel : k)), ...(hasNotes ? ['Notes'] : [])];
  const rows = items.map((it, i) => [
    it.n,
    ...(hasDesc ? [it.desc] : []),
    ...keys.map(k => (k === qty ? qtyCell(it) : it.fields[k] ?? '')),
    ...(hasNotes ? [notes[i]] : []),
  ]);
  const sharedRows: [string, string][] = shared.filter(k => k !== unit).map(k => [k, items[0].fields[k]]);
  if (sharedNote) sharedRows.push(['Notes', sharedNote]);
  return [
    ...(sharedRows.length ? [{ kind: 'facts' as const, rows: sharedRows, label: `All ${items.length} items` }] : []),
    { kind: 'items', columns, rows },
  ];
}

export function parseNoticeText(text: string): NoticeBlock[] {
  const blocks: NoticeBlock[] = [];
  let pendingItems: Item[] = [];
  const flushItems = () => {
    if (pendingItems.length >= 2) blocks.push(...itemsBlock(pendingItems));
    else if (pendingItems.length === 1) {
      const it = pendingItems[0];
      blocks.push({ kind: 'heading', text: `Item ${it.n}${it.desc ? ` — ${it.desc}` : ''}` });
      const rows = Object.entries(it.fields);
      if (rows.length) blocks.push({ kind: 'facts', rows });
      if (it.notes.length) blocks.push({ kind: 'paragraph', text: it.notes.join('\n') });
    }
    pendingItems = [];
  };

  for (const chunk of normalize(text).split(/\n\s*\n/)) {
    const lines = chunk.split('\n').filter(l => l.trim());
    if (!lines.length) continue;
    const item = parseItem(lines.map(l => l.trim()));
    if (item) { pendingItems.push(item); continue; }
    flushItems();

    let para: string[] = [];
    let facts: [string, string][] = [];
    let list: string[] = [];
    const flush = () => {
      if (para.length) blocks.push({ kind: 'paragraph', text: para.join('\n') });
      if (facts.length) blocks.push({ kind: 'facts', rows: facts });
      if (list.length) blocks.push({ kind: 'list', items: list });
      para = []; facts = []; list = [];
    };
    lines.forEach((raw, i) => {
      const line = raw.trim();
      // a short unpunctuated first line over more text is a title: "Debriefings"
      if (i === 0 && lines.length > 1 && line.length <= 60 && !/[.;:,!?]$/.test(line) && !kv(line)
          && !BULLET_RE.test(raw) && /^[A-Z“"(]/.test(line)) {
        blocks.push({ kind: 'heading', text: line });
        return;
      }
      const f = kv(line);
      const b = BULLET_RE.exec(raw);
      if (f) { if (para.length || list.length) flush(); facts.push(f); }
      else if (b) { if (para.length || facts.length) flush(); list.push(b[1].trim()); }
      else if (isHeading(line)) { flush(); blocks.push({ kind: 'heading', text: line }); }
      else { if (facts.length || list.length) flush(); para.push(line); }
    });
    flush();
  }
  flushItems();
  return blocks;
}

// Rough visible size of a block, for the collapsed preview.
export function blockSize(b: NoticeBlock): number {
  switch (b.kind) {
    case 'heading': case 'paragraph': return b.text.length;
    case 'facts': return b.rows.length * 40;
    case 'list': return b.items.join(' ').length;
    case 'items': return Math.min(b.rows.length, 5) * 60;
  }
}
