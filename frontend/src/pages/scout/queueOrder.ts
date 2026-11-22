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

export function hasDeadline(t: Pick<Tender, 'deadline'>): boolean {
  return !!(t.deadline && t.deadline.trim());
}

// Deadline order puts tenders with no stated deadline FIRST, in their own
// labelled band (see deadlineBands): an unknown deadline could be tomorrow,
// so it must neither sink below every dated tender nor pass silently as one.
// The server's own order (api.list_tenders) puts them last; the queue
// re-orders client-side so the band can lead.
export function sortTenders<T extends Pick<Tender, 'deadline' | 'pub_date' | 'status'>>(list: T[], by: SortBy): T[] {
  const sorted = [...list];
  if (by === 'pub_date') {
    sorted.sort((a, b) => (b.pub_date || '').localeCompare(a.pub_date || ''));
  } else if (by === 'status') {
    sorted.sort((a, b) => (STATUS_SORT_RANK[a.status] ?? 99) - (STATUS_SORT_RANK[b.status] ?? 99));
  } else {
    sorted.sort((a, b) => {
      const ad = hasDeadline(a), bd = hasDeadline(b);
      if (ad !== bd) return ad ? 1 : -1;
      return ad ? a.deadline.localeCompare(b.deadline) : 0;
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
