// CR-017 §1.2: the Review Queue's ordering, kept free of React so it can be
// tested on its own (queueOrder.test.ts, `npm test`).
import type { Tender } from '../../types';

// CR-008 W4: 'status' sorts by decision (see STATUS_SORT_RANK below).
export type SortBy = 'pub_date' | 'deadline' | 'status';

// CR-017 §1.2: closing-soonest first is the default — 46 surfaced tenders
// expired undecided against 15 ever shortlisted under newest-first.
export const DEFAULT_SORT: SortBy = 'deadline';

// CR-008 W4: undecided-first, then decided-positive, then decided-negative —
// a more useful grouping than alphabetical when sorting "by decision".
export const STATUS_SORT_RANK: Record<string, number> = {
  new: 0, needs_review: 1, reviewed: 2, shortlisted: 3, dismissed: 4, dismissed_final: 5,
};

// Code-unit order, as Python compares strings on the server (localeCompare
// would order punctuation in pub_numbers differently from api.list_tenders).
export function cmp(a: string, b: string): number {
  return a < b ? -1 : a > b ? 1 : 0;
}

export function hasDeadline(t: Pick<Tender, 'deadline'>): boolean {
  return !!(t.deadline && t.deadline.trim());
}

// Deadline order puts tenders with no stated deadline FIRST, in their own
// labelled band (see deadlineBands): an unknown deadline could be tomorrow,
// so it must neither sink below every dated tender nor pass silently as one.
// The server's own order (api.list_tenders) puts them last; the queue
// re-orders client-side so the band can lead. Ties — same deadline, or both
// undated — break on pub_number (CR-017), matching the server, so the order
// is the same on every load.
export function sortTenders<T extends Pick<Tender, 'deadline' | 'pub_date' | 'status' | 'pub_number'>>(list: T[], by: SortBy): T[] {
  const sorted = [...list];
  if (by === 'pub_date') {
    sorted.sort((a, b) => (b.pub_date || '').localeCompare(a.pub_date || ''));
  } else if (by === 'status') {
    sorted.sort((a, b) => (STATUS_SORT_RANK[a.status] ?? 99) - (STATUS_SORT_RANK[b.status] ?? 99));
  } else {
    sorted.sort((a, b) => {
      const ad = hasDeadline(a), bd = hasDeadline(b);
      if (ad !== bd) return ad ? 1 : -1;
      return (ad ? cmp(a.deadline, b.deadline) : 0) || cmp(a.pub_number, b.pub_number);
    });
  }
  return sorted;
}

export interface Band<T> { label: string | null; tenders: T[] }

// Splits an already-sorted list into the bands the queue renders. Only the
// deadline order has bands; any other order is one unlabelled band.
export function deadlineBands<T extends Pick<Tender, 'deadline'>>(sorted: T[], by: SortBy): Band<T>[] {
  if (by !== 'deadline') return [{ label: null, tenders: sorted }];
  const undated = sorted.filter(t => !hasDeadline(t));
  const dated = sorted.filter(hasDeadline);
  const bands: Band<T>[] = [];
  if (undated.length) bands.push({ label: 'No deadline stated', tenders: undated });
  if (dated.length) bands.push({ label: undated.length ? 'By deadline, closing soonest first' : null, tenders: dated });
  return bands;
}

// CR-017 §1.3: whole days between an expired tender's deadline and `today`
// (both read as calendar dates, YYYY-MM-DD prefix) — for the expired view's
// "expired N days ago". `today` is a parameter so tests pin it.
export function daysSinceDeadline(deadline: string, today: string): number | null {
  const d = Date.parse(deadline.slice(0, 10) + 'T00:00:00Z');
  const t = Date.parse(today.slice(0, 10) + 'T00:00:00Z');
  if (Number.isNaN(d) || Number.isNaN(t)) return null;
  return Math.round((t - d) / 86_400_000);
}

export function expiredLabel(deadline: string, today: string): string {
  const n = daysSinceDeadline(deadline, today);
  if (n === null) return 'Expired';
  if (n <= 0) return 'Expired today';
  return n === 1 ? 'Expired yesterday' : `Expired ${n} days ago`;
}
