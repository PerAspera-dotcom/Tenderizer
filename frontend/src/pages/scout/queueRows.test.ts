// CR-017 §1.4 — run with `npm test`.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { applyExpiredRefresh, applyQueueRefresh, mergeRefreshed, pickSelection } from './queueRows.ts';

type R = { pub_number: string; status: string; deadline: string; pub_date: string; group?: number };
const r = (pub_number: string, status = 'new', deadline = '2026-09-01', group?: number): R =>
  ({ pub_number, status, deadline, pub_date: '2026-07-01', group });

test('replaces in-scope rows in place and leaves the rest as the same objects', () => {
  const a = r('A'), b = r('B'), c = r('C');
  const out = mergeRefreshed([a, b, c], { results: [r('B', 'shortlisted')], scope: ['B'] });
  assert.deepEqual(out.map(x => [x.pub_number, x.status]), [['A', 'new'], ['B', 'shortlisted'], ['C', 'new']]);
  assert.equal(out[0], a);
  assert.equal(out[2], c);
});

test('a decision that propagates across a duplicate group updates every member, not just the one clicked', () => {
  // G-B was clicked; the server propagated to G-A and G-C and scoped all three
  const screen = [r('X'), r('G-A', 'new', '2026-09-01', 3), r('G-B', 'new', '2026-09-02', 3), r('G-C', 'new', '2026-09-03', 3)];
  const fresh = { results: ['G-A', 'G-B', 'G-C'].map(p => r(p, 'shortlisted', screen.find(s => s.pub_number === p)!.deadline, 3)),
                  scope: ['G-A', 'G-B', 'G-C'] };
  const out = mergeRefreshed(screen, fresh);
  assert.deepEqual(out.filter(x => x.pub_number.startsWith('G-')).map(x => x.status), ['shortlisted', 'shortlisted', 'shortlisted']);
  assert.equal(out[0], screen[0]);
});

test('in-scope rows the server no longer returns are removed', () => {
  const out = mergeRefreshed([r('A'), r('B'), r('C')], { results: [r('A', 'dismissed')], scope: ['A', 'B'] });
  assert.deepEqual(out.map(x => x.pub_number), ['A', 'C']);
});

test('fresh rows that were not on screen are added', () => {
  const out = mergeRefreshed([r('A')], { results: [r('A'), r('Z')], scope: ['A', 'Z'] });
  assert.deepEqual(out.map(x => x.pub_number), ['A', 'Z']);
});

test('a regrouped member is refreshed even though it was not acted on', () => {
  const screen = [r('H-A', 'dismissed', '2026-09-01', 3), r('H-B', 'dismissed', '2026-09-02', 3), r('H-C', 'dismissed', '2026-09-03', 3)];
  const fresh = { results: [r('H-A', 'dismissed', '2026-09-01', 2), r('H-B', 'dismissed', '2026-09-02', 2), r('H-C', 'dismissed_final', '2026-09-03')],
                  scope: ['H-A', 'H-B', 'H-C'] };
  const out = applyQueueRefresh(screen, fresh, 'deadline');
  assert.deepEqual(out.map(x => [x.pub_number, x.group]), [['H-A', 2], ['H-B', 2]]);   // H-C left the queue
});

test('queue refresh drops final-dismissed rows and keeps the current sort', () => {
  const screen = [r('late', 'new', '2026-12-01'), r('soon', 'new', '2026-08-01')];
  const out = applyQueueRefresh(screen, { results: [r('mid', 'new', '2026-10-01')], scope: ['mid'] }, 'deadline');
  assert.deepEqual(out.map(x => x.pub_number), ['soon', 'mid', 'late']);
  const gone = applyQueueRefresh(out, { results: [r('mid', 'dismissed_final', '2026-10-01')], scope: ['mid'] }, 'deadline');
  assert.deepEqual(gone.map(x => x.pub_number), ['soon', 'late']);
});

test('expired refresh: a dismissed expired tender leaves the view and the total drops', () => {
  const expired = [r('E-2', 'new', '2026-07-10'), r('E-1', 'new', '2026-07-01')];
  const out = applyExpiredRefresh(expired, 46, { results: [], scope: ['E-1'] });
  assert.deepEqual(out.list.map(x => x.pub_number), ['E-2']);
  assert.equal(out.total, 45);
});

test('expired refresh keeps most recently expired first when a row comes back', () => {
  const out = applyExpiredRefresh([r('E-1', 'new', '2026-07-01')], 1, { results: [r('E-3', 'new', '2026-07-12')], scope: ['E-3'] });
  assert.deepEqual(out.list.map(x => x.pub_number), ['E-3', 'E-1']);
  assert.equal(out.total, 2);
});

test('selection follows the fresh copy, from either list', () => {
  const sel = r('A');
  assert.equal(pickSelection(sel, [{ results: [r('A', 'shortlisted')], scope: ['A'] }], null)!.status, 'shortlisted');
  assert.equal(pickSelection(sel, [{ results: [], scope: ['A'] }, { results: [r('A', 'needs_review')], scope: ['A'] }], null)!.status, 'needs_review');
});

test('selection falls back to the first row only when the selected tender left both lists', () => {
  const sel = r('A'), first = r('F');
  assert.equal(pickSelection(sel, [{ results: [], scope: ['A'] }, { results: [], scope: ['A'] }], first), first);
  assert.equal(pickSelection(sel, [{ results: [r('B')], scope: ['B'] }], first), sel);   // A untouched
});
