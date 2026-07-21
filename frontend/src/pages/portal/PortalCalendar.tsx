import { useEffect, useMemo, useState } from 'react';
import { Link } from '../../router';
import { listTenders, getPipeline } from '../../api';
import type { Tender, PipelineEntry } from '../../types';
import { daysLeft, displayTagLine } from '../../utils';

interface Tile {
  pub_number: string;
  title: string;
  buyer: string;
  source: string;
  url: string;
  status: string;
  date: string;   // YYYY-MM-DD, the effective deadline
  color: string;
  label: string;
}

type ViewMode = 'week' | 'month' | 'quarter' | 'year';

const GREY = '#4c5a70';
const AMBER = '#e3b341';
const RED = '#f87171';
const GREEN = '#34d399';

// Distinct from .card's #151d2c (globals.css) — day cells were reading as one
// flat surface against the card background before this.
const CELL_BG = '#182031';
const CELL_BG_OUT = '#0d121c';
const CELL_BORDER = '#28334a';
const TODAY_BG = 'rgba(46,230,212,0.10)';

// CR-002 F follow-up: colour is status-first (new/reviewed) — but "accepted"
// alone doesn't convey urgency, so shortlisted tiles are coloured by days-to-
// deadline instead, same red<=7/amber<=14/green scheme as the Pipeline page.
function tileColor(status: string, effectiveDeadline: string): { color: string; label: string } {
  if (status === 'shortlisted') {
    const d = daysLeft(effectiveDeadline);
    if (d !== null && d <= 7) return { color: RED, label: 'Accepted — closing soon' };
    if (d !== null && d <= 14) return { color: AMBER, label: 'Accepted — closing this fortnight' };
    return { color: GREEN, label: 'Accepted' };
  }
  if (status === 'reviewed') return { color: AMBER, label: 'Reviewed' };
  return { color: GREY, label: 'New — to review' };
}

function ymd(d: Date): string {
  return d.toISOString().slice(0, 10);
}

function startOfDay(d: Date): Date { return new Date(d.getFullYear(), d.getMonth(), d.getDate()); }
function addDays(d: Date, n: number): Date { const r = new Date(d); r.setDate(r.getDate() + n); return r; }
function addMonths(d: Date, n: number): Date { return new Date(d.getFullYear(), d.getMonth() + n, 1); }

function startOfWeek(d: Date): Date {
  const s = startOfDay(d);
  const dow = (s.getDay() + 6) % 7; // 0 = Monday
  return addDays(s, -dow);
}
function startOfMonth(d: Date): Date { return new Date(d.getFullYear(), d.getMonth(), 1); }
function startOfQuarter(d: Date): Date { return new Date(d.getFullYear(), Math.floor(d.getMonth() / 3) * 3, 1); }

// Monday-first 6-week grid covering the full month, matching how the rest of
// this app dates procurement (EU convention) rather than a Sunday-first grid.
function buildMonthGrid(monthStart: Date): Date[] {
  const firstWeekday = (monthStart.getDay() + 6) % 7;
  const gridStart = addDays(monthStart, -firstWeekday);
  return Array.from({ length: 42 }, (_, i) => addDays(gridStart, i));
}

function shiftCursor(cursor: Date, view: ViewMode, delta: number): Date {
  if (view === 'week') return addDays(cursor, delta * 7);
  if (view === 'month') return addMonths(cursor, delta);
  if (view === 'quarter') return addMonths(cursor, delta * 3);
  return new Date(cursor.getFullYear() + delta, cursor.getMonth(), 1);
}

function periodLabel(cursor: Date, view: ViewMode): string {
  const fmt = (d: Date, withYear = false) =>
    d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', ...(withYear ? { year: 'numeric' } : {}) });
  if (view === 'week') {
    const s = startOfWeek(cursor);
    return `${fmt(s)} – ${fmt(addDays(s, 6), true)}`;
  }
  if (view === 'month') return cursor.toLocaleDateString('en-GB', { month: 'long', year: 'numeric' });
  if (view === 'quarter') {
    const qs = startOfQuarter(cursor);
    return `Q${Math.floor(qs.getMonth() / 3) + 1} ${qs.getFullYear()} (${qs.toLocaleDateString('en-GB', { month: 'short' })} – ${addMonths(qs, 2).toLocaleDateString('en-GB', { month: 'short' })})`;
  }
  return String(cursor.getFullYear());
}

const VIEW_OPTS: { value: ViewMode; label: string }[] = [
  { value: 'week', label: 'Week' },
  { value: 'month', label: 'Month' },
  { value: 'quarter', label: 'Quarter' },
  { value: 'year', label: 'Year' },
];

const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const WEEKDAYS_MINI = ['M', 'T', 'W', 'T', 'F', 'S', 'S'];

// Minimized event bar — a slim colour-accented strip, title only. Tooltip
// carries buyer/status detail that no longer fits in the bar itself.
function DayTile({ tile, dense }: { tile: Tile; dense?: boolean }) {
  return (
    <a
      href={tile.url || undefined}
      target="_blank"
      rel="noopener noreferrer"
      title={`${tile.title}\n${tile.buyer}\n${tile.label}`}
      style={{
        display: 'block', fontSize: dense ? 9.5 : 10.5, lineHeight: 1.4,
        padding: dense ? '0px 4px' : '1px 5px', marginBottom: 2, borderRadius: 2,
        background: `${tile.color}1f`, color: tile.color, borderLeft: `2px solid ${tile.color}`,
        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', textDecoration: 'none',
        cursor: tile.url ? 'pointer' : 'default',
      }}
    >
      {tile.title}
    </a>
  );
}

// Year view has no room for title text at all — a row of colour dots
// (hover for the full list) stands in for the minimized bars.
function DayDots({ tiles }: { tiles: Tile[] }) {
  if (tiles.length === 0) return null;
  return (
    <div title={tiles.map(t => t.title).join('\n')} style={{ display: 'flex', gap: 2, flexWrap: 'wrap', marginTop: 2 }}>
      {tiles.slice(0, 5).map(t => (
        <span key={t.pub_number} style={{ width: 5, height: 5, borderRadius: '50%', background: t.color, flexShrink: 0 }} />
      ))}
    </div>
  );
}

interface DayCellProps {
  date: Date; inPeriod: boolean; isToday: boolean; tiles: Tile[];
  height: number; maxTiles: number; showDots?: boolean;
}
// CR-002 F follow-up: a genuinely FIXED size per day — height (not
// minHeight) + overflow hidden, so a busy day never grows the row; excess
// tenders collapse into a "+N more" line instead.
function DayCell({ date, inPeriod, isToday, tiles, height, maxTiles, showDots }: DayCellProps) {
  const shown = tiles.slice(0, maxTiles);
  const overflow = tiles.length - shown.length;
  return (
    <div style={{
      height, padding: 4, overflow: 'hidden',
      borderBottom: `1px solid ${CELL_BORDER}`, borderRight: `1px solid ${CELL_BORDER}`,
      background: isToday ? TODAY_BG : inPeriod ? CELL_BG : CELL_BG_OUT,
    }}>
      <div style={{ fontSize: 10.5, color: isToday ? '#2EE6D4' : inPeriod ? '#8892a4' : '#4c5a70', fontWeight: isToday ? 700 : 400, marginBottom: 2 }}>
        {date.getDate()}
      </div>
      {showDots ? <DayDots tiles={tiles} /> : (
        <>
          {shown.map(tile => <DayTile key={tile.pub_number} tile={tile} dense={maxTiles <= 2} />)}
          {overflow > 0 && <div style={{ fontSize: 9, color: '#8892a4' }}>+{overflow} more</div>}
        </>
      )}
    </div>
  );
}

interface GridProps {
  days: Date[]; monthStart: Date | null; tilesByDate: Map<string, Tile[]>; today: string;
  cellHeight: number; maxTiles: number; showDots?: boolean; showWeekdays?: boolean; weekdayLabels?: string[];
}
function CalendarGrid({ days, monthStart, tilesByDate, today, cellHeight, maxTiles, showDots, showWeekdays = true, weekdayLabels = WEEKDAYS }: GridProps) {
  return (
    <div>
      {showWeekdays && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7, 1fr)' }}>
          {weekdayLabels.map((w, i) => (
            <div key={`${w}-${i}`} style={{ padding: '6px 8px', fontSize: 10, fontWeight: 700, letterSpacing: '0.06em', color: '#4c5a70', textTransform: 'uppercase', borderBottom: `1px solid ${CELL_BORDER}` }}>
              {w}
            </div>
          ))}
        </div>
      )}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7, 1fr)' }}>
        {days.map(d => {
          const dateStr = ymd(d);
          return (
            <DayCell
              key={dateStr}
              date={d}
              inPeriod={monthStart ? d.getMonth() === monthStart.getMonth() : true}
              isToday={dateStr === today}
              tiles={tilesByDate.get(dateStr) ?? []}
              height={cellHeight}
              maxTiles={maxTiles}
              showDots={showDots}
            />
          );
        })}
      </div>
    </div>
  );
}

// CR-002 F follow-up: new top-level Portal landing — tenders' deadlines by
// week/month/quarter/year, tiles for ALL active tenders (not just
// shortlisted), coloured by review/urgency status.
export default function PortalCalendar() {
  const [tenders, setTenders] = useState<Tender[]>([]);
  const [pipeline, setPipeline] = useState<PipelineEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [view, setView] = useState<ViewMode>('month');
  const [cursor, setCursor] = useState(() => startOfDay(new Date()));

  useEffect(() => {
    Promise.all([listTenders({ limit: 1000 }), getPipeline()])
      .then(([t, p]) => { setTenders(t.results); setPipeline(p); })
      .catch(() => setError('Failed to load calendar data'))
      .finally(() => setLoading(false));
  }, []);

  const tiles = useMemo<Tile[]>(() => {
    const overrideByPub = new Map(pipeline.map(e => [e.pub_number, e.deadline_override]));
    return tenders
      .filter(t => t.status !== 'dismissed')
      .map(t => {
        const effective = (t.status === 'shortlisted' && overrideByPub.get(t.pub_number)) || t.deadline;
        return { t, effective };
      })
      .filter(({ effective }) => !!effective)
      .map(({ t, effective }) => {
        const { color, label } = tileColor(t.status, effective);
        return {
          pub_number: t.pub_number, title: displayTagLine(t), buyer: t.buyer, source: t.source,
          url: t.url, status: t.status, date: effective.slice(0, 10), color, label,
        };
      });
  }, [tenders, pipeline]);

  const tilesByDate = useMemo(() => {
    const map = new Map<string, Tile[]>();
    for (const tile of tiles) {
      const list = map.get(tile.date) ?? [];
      list.push(tile);
      map.set(tile.date, list);
    }
    return map;
  }, [tiles]);

  const today = ymd(new Date());
  const label = periodLabel(cursor, view);

  // CR-002 F/D-D follow-up: this was Portal Home's job (a separate landing
  // page whose only other content — Scout/Vault/Composer launch cards —
  // duplicated the sidebar's own app-switcher one click away). Calendar is
  // the actual landing screen, so the one thing Home did that wasn't
  // duplicated elsewhere — urgent-deadline alerts — lives here instead now.
  const alerts = useMemo(() => {
    return pipeline
      .filter(e => e.submission_status !== 'submitted')
      .map(e => ({ e, d: daysLeft(e.deadline_override || e.deadline) }))
      .filter((x): x is { e: PipelineEntry; d: number } => x.d !== null && x.d <= 14)
      .sort((a, b) => a.d - b.d);
  }, [pipeline]);

  if (loading) return <div className="loading">Loading…</div>;
  if (error) return <div className="error">{error}</div>;

  return (
    <div>
      <h1 style={{ fontSize: 28, fontWeight: 700, marginBottom: 4 }}>Calendar</h1>
      <p style={{ color: '#8892a4', marginBottom: 16 }}>Every active tender by deadline — triage status and submission urgency at a glance</p>

      {/* Deadline alerts */}
      {alerts.length > 0 && (
        <div style={{ marginBottom: 16 }}>
          {alerts.map(({ e, d }) => {
            const isUrgent = d <= 7;
            return (
              <div
                key={e.pub_number}
                style={{
                  background: isUrgent ? 'rgba(248,113,113,0.08)' : 'rgba(227,179,65,0.08)',
                  border: `1px solid ${isUrgent ? 'rgba(248,113,113,0.3)' : 'rgba(227,179,65,0.3)'}`,
                  borderRadius: 8, padding: '10px 16px', marginBottom: 8,
                  display: 'flex', alignItems: 'center', gap: 12,
                }}
              >
                <span style={{ fontSize: 15 }}>{isUrgent ? '🔴' : '🟡'}</span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontWeight: 500, fontSize: 13, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {displayTagLine(e)}
                  </div>
                  <div style={{ fontSize: 12, color: isUrgent ? '#f87171' : '#e3b341', marginTop: 2 }}>
                    {isUrgent
                      ? `Closes in ${d} day${d !== 1 ? 's' : ''} and no tender has been sent — act now or request an extension.`
                      : `Deadline in ${d} day${d !== 1 ? 's' : ''} — submission in progress`}
                  </div>
                </div>
                <Link to="/portal/pipeline" style={{ color: '#2EE6D4', fontSize: 13, fontWeight: 500, whiteSpace: 'nowrap' }}>View →</Link>
              </div>
            );
          })}
        </div>
      )}

      {/* Legend */}
      <div style={{ display: 'flex', gap: 16, marginBottom: 16, flexWrap: 'wrap', fontSize: 12, color: '#8892a4' }}>
        <span><span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', background: GREY, marginRight: 6 }} />New — to review</span>
        <span><span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', background: AMBER, marginRight: 6 }} />Reviewed</span>
        <span><span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', background: GREEN, marginRight: 6 }} />Accepted</span>
        <span><span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', background: AMBER, marginRight: 6 }} />Accepted, ≤14 days</span>
        <span><span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', background: RED, marginRight: 6 }} />Accepted, ≤7 days</span>
      </div>

      {/* Nav + view selector */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginBottom: 12, flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <button className="btn btn-ghost" onClick={() => setCursor(c => shiftCursor(c, view, -1))} style={{ fontSize: 13 }}>‹ Prev</button>
          <div style={{ fontWeight: 600, fontSize: 15, minWidth: 200, textAlign: 'center' }}>{label}</div>
          <button className="btn btn-ghost" onClick={() => setCursor(c => shiftCursor(c, view, 1))} style={{ fontSize: 13 }}>Next ›</button>
          <button className="btn btn-ghost" onClick={() => setCursor(startOfDay(new Date()))} style={{ fontSize: 13 }}>Today</button>
        </div>
        <div style={{ display: 'flex', gap: 6, marginLeft: 'auto' }}>
          {VIEW_OPTS.map(o => {
            const isActive = view === o.value;
            return (
              <button
                key={o.value}
                onClick={() => setView(o.value)}
                style={{
                  padding: '6px 14px', borderRadius: 6, fontSize: 13, fontWeight: 500,
                  border: `1px solid ${isActive ? '#2EE6D4' : '#1a2334'}`,
                  background: isActive ? 'rgba(46,230,212,0.12)' : 'transparent',
                  color: isActive ? '#2EE6D4' : '#8892a4',
                }}
              >
                {o.label}
              </button>
            );
          })}
        </div>
      </div>

      {/* Grid */}
      <div className="card" style={{ overflow: 'hidden', padding: view === 'quarter' || view === 'year' ? 16 : 0 }}>
        {view === 'week' && (
          <CalendarGrid
            days={Array.from({ length: 7 }, (_, i) => addDays(startOfWeek(cursor), i))}
            monthStart={null} tilesByDate={tilesByDate} today={today}
            cellHeight={240} maxTiles={10}
          />
        )}

        {view === 'month' && (
          <CalendarGrid
            days={buildMonthGrid(startOfMonth(cursor))}
            monthStart={startOfMonth(cursor)} tilesByDate={tilesByDate} today={today}
            cellHeight={110} maxTiles={4}
          />
        )}

        {view === 'quarter' && (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 16 }}>
            {[0, 1, 2].map(i => {
              const monthStart = addMonths(startOfQuarter(cursor), i);
              return (
                <div key={i}>
                  <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 8, textAlign: 'center' }}>
                    {monthStart.toLocaleDateString('en-GB', { month: 'long', year: 'numeric' })}
                  </div>
                  <CalendarGrid
                    days={buildMonthGrid(monthStart)} monthStart={monthStart} tilesByDate={tilesByDate} today={today}
                    cellHeight={48} maxTiles={1} weekdayLabels={WEEKDAYS_MINI}
                  />
                </div>
              );
            })}
          </div>
        )}

        {view === 'year' && (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 16 }}>
            {Array.from({ length: 12 }, (_, i) => new Date(cursor.getFullYear(), i, 1)).map(monthStart => (
              <div key={monthStart.getMonth()}>
                <div style={{ fontWeight: 600, fontSize: 12, marginBottom: 6, textAlign: 'center' }}>
                  {monthStart.toLocaleDateString('en-GB', { month: 'short' })}
                </div>
                <CalendarGrid
                  days={buildMonthGrid(monthStart)} monthStart={monthStart} tilesByDate={tilesByDate} today={today}
                  cellHeight={22} maxTiles={0} showDots showWeekdays={false}
                />
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
