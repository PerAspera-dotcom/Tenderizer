// CR-017 §1.2 — run with `npm test` (Node's built-in runner, no extra deps).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { DEFAULT_SORT, deadlineBands, sortTenders } from './queueOrder.ts';

const t = (pub: string, deadline: string, pub_date = '2026-07-01', status = 'new') =>
  ({ pub_number: pub, deadline, pub_date, status });

const LIST = [
  t('late', '2026-09-01', '2026-07-10'),
  t('none-a', '', '2026-07-12'),
  t('soon', '2026-07-16T10:00:00', '2026-07-01'),
  t('blank', '   ', '2026-07-05'),
  t('mid', '2026-08-01', '2026-07-03'),
  t('none-b', '', '2026-07-02'),
];

test('the default order is deadline', () => {
  assert.equal(DEFAULT_SORT, 'deadline');
});

test('deadline order: no-deadline tenders first, then closing soonest first', () => {
  const pubs = sortTenders(LIST, 'deadline').map(x => x.pub_number);
  assert.deepEqual(pubs.slice(0, 3).sort(), ['blank', 'none-a', 'none-b']);
  assert.deepEqual(pubs.slice(3), ['soon', 'mid', 'late']);
});

test('deadline order keeps the server order within the no-deadline band', () => {
  const pubs = sortTenders(LIST, 'deadline').map(x => x.pub_number);
  assert.deepEqual(pubs.slice(0, 3), ['none-a', 'blank', 'none-b']);
});

test('the no-deadline band is labelled, and the dated band says what it is', () => {
  const bands = deadlineBands(sortTenders(LIST, 'deadline'), 'deadline');
  assert.deepEqual(bands.map(b => b.label), ['No deadline stated', 'By deadline, closing soonest first']);
  assert.deepEqual(bands.map(b => b.tenders.length), [3, 3]);
});

test('no band labels when every tender has a deadline', () => {
  const dated = LIST.filter(x => x.deadline.trim());
  const bands = deadlineBands(sortTenders(dated, 'deadline'), 'deadline');
  assert.deepEqual(bands.map(b => b.label), [null]);
});

test('only an undated band when nothing has a deadline', () => {
  const bands = deadlineBands(sortTenders(LIST.filter(x => !x.deadline.trim()), 'deadline'), 'deadline');
  assert.deepEqual(bands.map(b => b.label), ['No deadline stated']);
});

test('other orders are one unlabelled band and unchanged', () => {
  const byRelease = sortTenders(LIST, 'pub_date');
  assert.deepEqual(byRelease.map(x => x.pub_number), ['none-a', 'late', 'blank', 'mid', 'none-b', 'soon']);
  assert.deepEqual(deadlineBands(byRelease, 'pub_date'), [{ label: null, tenders: byRelease }]);
});

test('no tender is lost or duplicated by banding', () => {
  const bands = deadlineBands(sortTenders(LIST, 'deadline'), 'deadline');
  const all = bands.flatMap(b => b.tenders.map(x => x.pub_number)).sort();
  assert.deepEqual(all, LIST.map(x => x.pub_number).sort());
});
