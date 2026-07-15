import { useEffect, useMemo, useState } from 'react';
import { listTenders, getPipeline } from '../../api';
import type { Tender, PipelineEntry } from '../../types';
import { daysLeft } from '../../utils';

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

const GREY = '#4c5a70';
const AMBER = '#e3b341';
const RED = '#f87171';
const GREEN = '#34d399';

// CR-002 F: colour is status-first (new/reviewed) — but "accepted" alone
// doesn't convey urgency, so shortlisted tiles are coloured by days-to-
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

function startOfMonth(d: Date): Date { return new Date(d.getFullYear(), d.getMonth(), 1); }

// Monday-first 6-week grid covering the full month, matching how the rest of
// this app dates procurement (EU convention) rather than a Sunday-first grid.
function buildGrid(monthCursor: Date): Date[] {
  const first = startOfMonth(monthCursor);
  const firstWeekday = (first.getDay() + 6) % 7; // 0 = Monday
  const gridStart = new Date(first);
  gridStart.setDate(first.getDate() - firstWeekday);
  return Array.from({ length: 42 }, (_, i) => {
    const d = new Date(gridStart);
    d.setDate(gridStart.getDate() + i);
    return d;
  });
}

const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

// CR-002 F: new top-level Portal landing — a month grid over ALL active
// tenders' deadlines (not just shortlisted), coloured by review/urgency
// status, so the analyst sees triage + submission workload at a glance.
export default function PortalCalendar() {
  const [tenders, setTenders] = useState<Tender[]>([]);
  const [pipeline, setPipeline] = useState<PipelineEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [monthCursor, setMonthCursor] = useState(() => startOfMonth(new Date()));

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
          pub_number: t.pub_number, title: t.tag_line, buyer: t.buyer, source: t.source,
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

  const grid = useMemo(() => buildGrid(monthCursor), [monthCursor]);
  const today = ymd(new Date());
  const monthLabel = monthCursor.toLocaleDateString('en-GB', { month: 'long', year: 'numeric' });

  function shiftMonth(delta: number) {
    setMonthCursor(prev => new Date(prev.getFullYear(), prev.getMonth() + delta, 1));
  }

  if (loading) return <div className="loading">Loading…</div>;
  if (error) return <div className="error">{error}</div>;

  return (
    <div>
      <h1 style={{ fontSize: 28, fontWeight: 700, marginBottom: 4 }}>Calendar</h1>
      <p style={{ color: '#8892a4', marginBottom: 16 }}>Every active tender by deadline — triage status and submission urgency at a glance</p>

      {/* Legend */}
      <div style={{ display: 'flex', gap: 16, marginBottom: 16, flexWrap: 'wrap', fontSize: 12, color: '#8892a4' }}>
        <span><span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', background: GREY, marginRight: 6 }} />New — to review</span>
        <span><span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', background: AMBER, marginRight: 6 }} />Reviewed</span>
        <span><span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', background: GREEN, marginRight: 6 }} />Accepted</span>
        <span><span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', background: AMBER, marginRight: 6 }} />Accepted, ≤14 days</span>
        <span><span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', background: RED, marginRight: 6 }} />Accepted, ≤7 days</span>
      </div>

      {/* Month nav */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
        <button className="btn btn-ghost" onClick={() => shiftMonth(-1)} style={{ fontSize: 13 }}>‹ Prev</button>
        <div style={{ fontWeight: 600, fontSize: 15, minWidth: 160, textAlign: 'center' }}>{monthLabel}</div>
        <button className="btn btn-ghost" onClick={() => shiftMonth(1)} style={{ fontSize: 13 }}>Next ›</button>
        <button className="btn btn-ghost" onClick={() => setMonthCursor(startOfMonth(new Date()))} style={{ fontSize: 13 }}>Today</button>
      </div>

      {/* Grid */}
      <div className="card" style={{ overflow: 'hidden' }}>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7, 1fr)' }}>
          {WEEKDAYS.map(w => (
            <div key={w} style={{ padding: '8px 10px', fontSize: 11, fontWeight: 700, letterSpacing: '0.08em', color: '#4c5a70', textTransform: 'uppercase', borderBottom: '1px solid #1a2334' }}>
              {w}
            </div>
          ))}
          {grid.map(d => {
            const dateStr = ymd(d);
            const inMonth = d.getMonth() === monthCursor.getMonth();
            const dayTiles = tilesByDate.get(dateStr) ?? [];
            const isToday = dateStr === today;
            const shown = dayTiles.slice(0, 3);
            const overflow = dayTiles.length - shown.length;
            return (
              <div
                key={dateStr}
                style={{
                  minHeight: 92, padding: 6, borderBottom: '1px solid #1a2334', borderRight: '1px solid #1a2334',
                  background: isToday ? 'rgba(46,230,212,0.04)' : 'transparent', opacity: inMonth ? 1 : 0.35,
                }}
              >
                <div style={{ fontSize: 11, color: isToday ? '#2EE6D4' : '#8892a4', fontWeight: isToday ? 700 : 400, marginBottom: 4 }}>
                  {d.getDate()}
                </div>
                {shown.map(tile => (
                  <a
                    key={tile.pub_number}
                    href={tile.url || undefined}
                    target="_blank"
                    rel="noopener noreferrer"
                    title={`${tile.title}\n${tile.buyer}\n${tile.label}`}
                    style={{
                      display: 'block', fontSize: 10.5, padding: '2px 5px', marginBottom: 2, borderRadius: 3,
                      background: `${tile.color}1a`, color: tile.color, border: `1px solid ${tile.color}4d`,
                      overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', textDecoration: 'none',
                      cursor: tile.url ? 'pointer' : 'default',
                    }}
                  >
                    {tile.title}
                  </a>
                ))}
                {overflow > 0 && (
                  <div style={{ fontSize: 10, color: '#8892a4' }}>+{overflow} more</div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
