// CR-017 §1.4: update the Review Queue in place after an action, instead of
// refetching every row. Pure, so it's tested on its own (queueRows.test.ts).
//
// The server does the hard part: GET /api/tenders?pub=X returns X and every
// tender duplicate-linked to it, each identical to its row in a full
// reload, plus `scope` — every pub_number it considered. A decision on one
// member of a duplicate group propagates to the whole group, and resolving
// or dismissing a member changes the others' group size and canonical, so
// the client must replace EVERY row in scope, not just the one clicked, and
// drop any that no longer qualify. Otherwise rows stay on screen looking
// decided (or undecided) when they aren't.
//
// Not refreshed, by decision (2026-11-22): other rows' relevance scores. A
// shortlist or final dismiss changes the scoring pools, so they drift until
// the next full load — preferred to numbers changing under the reviewer.
import type { Tender } from '../../types';
import { cmp, sortTenders, type SortBy } from './queueOrder.ts';

export interface Refresh<T> { results: T[]; scope?: string[] }

type Row = Pick<Tender, 'pub_number'>;

// Replaces every in-scope row with its fresh version (keeping its place),
// drops in-scope rows the server no longer returns, appends fresh rows that
// weren't on screen. Rows outside the scope are returned untouched (same
// objects).
export function mergeRefreshed<T extends Row>(list: T[], fresh: Refresh<T>): T[] {
  const scope = new Set(fresh.scope ?? fresh.results.map(r => r.pub_number));
  const byPub = new Map(fresh.results.map(r => [r.pub_number, r] as const));
  const out: T[] = [];
  for (const t of list) {
    if (!scope.has(t.pub_number)) {
      out.push(t);
    } else if (byPub.has(t.pub_number)) {
      out.push(byPub.get(t.pub_number)!);
      byPub.delete(t.pub_number);
    }
  }
  out.push(...byPub.values());
  return out;
}

// The queue list after a refresh: merged, final-dismissed rows removed (as
// load() does — CR-007 B1), re-sorted in the reviewer's current order.
export function applyQueueRefresh<T extends Row & Pick<Tender, 'status' | 'deadline' | 'pub_date'>>(
  tenders: T[], fresh: Refresh<T>, sortBy: SortBy,
): T[] {
  return sortTenders(mergeRefreshed(tenders, fresh).filter(t => t.status !== 'dismissed_final'), sortBy);
}

// The expired list after a refresh, most recently expired first (the
// server's order), and its total adjusted by the rows gained or lost — the
// list may be a first page of a longer total.
export function applyExpiredRefresh<T extends Row & Pick<Tender, 'deadline'>>(
  expired: T[], total: number, fresh: Refresh<T>,
): { list: T[]; total: number } {
  const list = mergeRefreshed(expired, fresh).sort((a, b) => cmp(b.deadline, a.deadline) || cmp(a.pub_number, b.pub_number));
  return { list, total: Math.max(0, total + list.length - expired.length) };
}

// What the detail pane should show after a refresh: the fresh copy of the
// selected tender if either list returned it (a just-final-dismissed tender
// is still in the queue response, so it stays on screen — CR-002 C2), else,
// if it fell out of both, the first row of the current view, as a full
// reload would have done.
export function pickSelection<T extends Row>(
  selected: T | null, freshLists: Refresh<T>[], fallback: T | null,
): T | null {
  if (!selected) return fallback;
  for (const f of freshLists) {
    const hit = f.results.find(r => r.pub_number === selected.pub_number);
    if (hit) return hit;
  }
  const inScope = freshLists.some(f => (f.scope ?? []).includes(selected.pub_number));
  return inScope ? fallback : selected;
}
