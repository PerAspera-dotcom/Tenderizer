import { useEffect, useRef, useState } from 'react';
import { listTenders, patchTender, patchTenderRelevance, postTenderPresence, getScoutSettings, getOrgMembers, type OrgMember } from '../../api';
import type { Tender } from '../../types';
import { formatDate, countryFlag, confidenceFromMatchSource, formatValue, needsTranslation, hasTranslatedTagLine, hasTranslatedDescription, displayTagLine, displayDescription, hoursLeft } from '../../utils';
import MatchChip from '../../components/MatchChip';
import NoticeTypeBadge from '../../components/NoticeTypeBadge';
import ForwardTender from '../../components/ForwardTender';

type SortBy = 'pub_date' | 'deadline';
// CR-007 Phase B (B1/B2): 'dismissed' is now stage 1/soft (still queued,
// greyed out); 'dismissed_final' is stage 2 (the Dismissed tab).
type StatusFilter = 'all' | 'new' | 'shortlisted' | 'reviewed' | 'needs_review';

// CR-007 Phase B (B3): mirrors schema.RELEVANCE_REASON_CATEGORIES — required
// alongside a dismiss reason so relevance scoring can aggregate on it.
// Post-CR-007: sentinel for the assignee <select>'s "enter manually" option
// — mirrors ForwardTender.tsx's identical MANUAL_ENTRY.
const MANUAL_ASSIGNEE = '__manual__';

const REASON_CATEGORIES: { value: string; label: string }[] = [
  { value: 'wrong_sector', label: 'Wrong sector/CPV mismatch' },
  { value: 'value_too_low', label: 'Value too low' },
  { value: 'wrong_region', label: 'Wrong region/country' },
  { value: 'excluded_type', label: 'Excluded type (e.g. rental)' },
  { value: 'duplicate', label: 'Duplicate/republished' },
  { value: 'deadline_missed', label: 'Deadline missed' },
  { value: 'other', label: 'Other' },
];

function statusDotColor(status: string): string {
  if (status === 'shortlisted') return '#34d399';
  if (status === 'reviewed') return '#e3b341';
  if (status === 'needs_review') return '#c084fc';
  if (status === 'dismissed' || status === 'dismissed_final') return '#f87171';
  return '#4c5a70';
}

function StatusBadge({ status }: { status: string }) {
  const label =
    status === 'shortlisted' ? '● Shortlisted' :
    status === 'reviewed' ? '● Reviewed' :
    status === 'needs_review' ? '● Needs further review' :
    status === 'dismissed' ? '● Dismissed — pending final' :
    status === 'dismissed_final' ? '● Dismissed' : '○ New — awaiting decision';
  const color =
    status === 'shortlisted' ? '#34d399' :
    status === 'reviewed' ? '#e3b341' :
    status === 'needs_review' ? '#c084fc' :
    status === 'dismissed' || status === 'dismissed_final' ? '#f87171' : '#8892a4';
  return (
    <span style={{ background: `rgba(0,0,0,0.2)`, border: `1px solid ${color}`, color, borderRadius: 9999, padding: '3px 10px', fontSize: 12, fontWeight: 500 }}>
      {label}
    </span>
  );
}

function confidenceLabel(ms: string | null | undefined): string {
  if (!ms || ms === 'None' || ms === 'none') return 'Low confidence — no direct match';
  if (ms === 'both') return 'High confidence — matched by CPV + keywords';
  if (ms === 'cpv') return 'High confidence — matched by CPV code';
  if (ms === 'keyword') return 'Candidate — keyword match only';
  return ms;
}

export default function ReviewQueue() {
  const [tenders, setTenders] = useState<Tender[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [selected, setSelected] = useState<Tender | null>(null);
  const [patching, setPatching] = useState(false);
  const [showOriginal, setShowOriginal] = useState(false);
  // CR-002 C3: publication_date (newest first) is the Review Queue's default order.
  const [sortBy, setSortBy] = useState<SortBy>('pub_date');
  // CR-007 Phase D (D2): fetched once — the tenant's "closing soon" window,
  // formerly a hard 72h exclude, now purely a display threshold.
  const [deadlineFloorHours, setDeadlineFloorHours] = useState(72);
  useEffect(() => { getScoutSettings().then(s => setDeadlineFloorHours(s.deadline_floor_hours)).catch(() => {}); }, []);
  // Post-CR-007: the real Clerk org roster (self excluded) for the
  // needs_review assignee picker — see ForwardTender.tsx's identical use.
  const [orgMembers, setOrgMembers] = useState<OrgMember[]>([]);
  useEffect(() => { getOrgMembers().then(setOrgMembers).catch(() => setOrgMembers([])); }, []);

  function sortTenders(list: Tender[], by: SortBy): Tender[] {
    const sorted = [...list];
    if (by === 'pub_date') {
      sorted.sort((a, b) => (b.pub_date || '').localeCompare(a.pub_date || ''));
    } else {
      sorted.sort((a, b) => (a.deadline || '9999').localeCompare(b.deadline || '9999'));
    }
    return sorted;
  }

  function load() {
    listTenders({ limit: 500 }).then(r => {
      // CR-007 Phase B (B1): only the final dismiss stage leaves the queue —
      // a soft-dismissed ("dismissed") tender stays, greyed out (see the row
      // rendering below).
      const filtered = r.results.filter(t => t.status !== 'dismissed_final');
      setTenders(sortTenders(filtered, sortBy));
      setSelected(prev => {
        if (!prev) return filtered[0] ?? null;
        // CR-002 C2: look up the previous selection in the FULL result set, not
        // just `filtered` — a tender just dismissed drops out of the left list,
        // but the detail pane should still show it (with its dismiss note) until
        // the user picks something else, rather than silently jumping away.
        return r.results.find(t => t.pub_number === prev.pub_number) ?? filtered[0] ?? null;
      });
    }).catch(() => setError('Failed to load tenders'))
      .finally(() => setLoading(false));
  }

  // CR-006 D2 / CR-007 B1-B2: a required-note action in progress — dismiss
  // (either stage) or "needs further review". Generalizes what used to be a
  // dismiss-only inline panel (dismissOpen/dismissNote) into one shared
  // shape so all three actions reuse the same UI and required-field guard.
  const [noteAction, setNoteAction] = useState<
    null | { status: string; label: string; requireCategory: boolean; allowAssignee: boolean }
  >(null);
  const [noteText, setNoteText] = useState('');
  const [reasonCategory, setReasonCategory] = useState('');
  // Post-CR-007: optional colleague to ping — only surfaced on a needs_review
  // parking. assigneeManual mirrors ForwardTender.tsx's MANUAL_ENTRY toggle —
  // falls back to free-text entry when there's no org roster to pick from.
  const [assignedTo, setAssignedTo] = useState('');
  const [assigneeManual, setAssigneeManual] = useState(false);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');

  function closeNoteAction() {
    setNoteAction(null);
    setNoteText('');
    setReasonCategory('');
    setAssignedTo('');
    setAssigneeManual(false);
  }

  function selectTender(t: Tender) {
    setSelected(t);
    setShowOriginal(false);   // CR-001 R3: default to the translation on a new selection
    closeNoteAction();
    setRelevanceEditing(false);
  }

  useEffect(() => { load(); }, []);
  useEffect(() => { setTenders(prev => sortTenders(prev, sortBy)); }, [sortBy]);

  // CR-007 Phase D (D1): presence heartbeat — pings immediately on selecting
  // a tender, then every 20s while it stays selected, so a colleague opening
  // the same tender sees "currently working" within ~20s. Same bounded-
  // setInterval-in-a-ref pattern Layout.tsx's run-progress poller uses, just
  // always-on (while a tender is open) rather than triggered by one action.
  const presenceRef = useRef<ReturnType<typeof setInterval> | null>(null);
  useEffect(() => {
    if (presenceRef.current) clearInterval(presenceRef.current);
    if (!selected) return;
    postTenderPresence(selected.pub_number).catch(() => {});
    presenceRef.current = setInterval(() => {
      postTenderPresence(selected.pub_number).catch(() => {});
    }, 20000);
    return () => { if (presenceRef.current) clearInterval(presenceRef.current); };
  }, [selected?.pub_number]);

  async function applyStatus(status: string, note?: string, category?: string, assignee?: string) {
    if (!selected || patching) return;
    setPatching(true);
    try {
      await patchTender(selected.pub_number, status, note, category, assignee);
      load();
    } finally {
      setPatching(false);
    }
  }

  function startDismiss() {
    // CR-007 B1: the second stage pre-fills the first stage's note/category
    // as a starting point (editable), rather than making the reviewer
    // re-type a reason they already gave.
    const isFinal = selected?.status === 'dismissed';
    setNoteAction({
      status: isFinal ? 'dismissed_final' : 'dismissed',
      label: isFinal ? 'Confirm final dismiss' : 'Dismiss',
      requireCategory: true,
      allowAssignee: false,
    });
    setNoteText(isFinal ? (selected?.dismissal_reason ?? '') : '');
    setReasonCategory(isFinal ? (selected?.dismissal_reason_category ?? '') : '');
  }

  function startNeedsReview() {
    setNoteAction({ status: 'needs_review', label: 'Needs further review', requireCategory: false, allowAssignee: true });
    setNoteText('');
    setReasonCategory('');
    setAssignedTo(selected?.assigned_to ?? '');
    setAssigneeManual(orgMembers.length === 0);
  }

  function confirmNoteAction() {
    if (!noteAction) return;
    const note = noteText.trim();
    if (!note) return;  // mirrors the API's 400
    if (noteAction.requireCategory && !reasonCategory) return;
    const assignee = noteAction.allowAssignee ? assignedTo.trim() : undefined;
    if (assignee && !assignee.includes('@')) return;  // mirrors the API's 422
    applyStatus(noteAction.status, note, noteAction.requireCategory ? reasonCategory : undefined, assignee || undefined);
    closeNoteAction();
  }

  // CR-007 Phase B (B3): reviewer correction to a computed relevance score.
  const [relevanceEditing, setRelevanceEditing] = useState(false);
  const [relevanceScoreInput, setRelevanceScoreInput] = useState('');
  const [relevanceNoteInput, setRelevanceNoteInput] = useState('');

  function startRelevanceCorrection() {
    setRelevanceEditing(true);
    setRelevanceScoreInput(String(selected?.relevance_score ?? 50));
    setRelevanceNoteInput('');
  }

  async function submitRelevanceCorrection() {
    if (!selected || patching) return;
    const score = Number(relevanceScoreInput);
    if (!Number.isFinite(score) || score < 0 || score > 100) return;
    setPatching(true);
    try {
      await patchTenderRelevance(selected.pub_number, score, relevanceNoteInput.trim() || undefined);
      setRelevanceEditing(false);
      load();
    } finally {
      setPatching(false);
    }
  }

  const newCount = tenders.filter(t => t.status === 'new').length;
  const shortlistedCount = tenders.filter(t => t.status === 'shortlisted').length;
  const reviewedCount = tenders.filter(t => t.status === 'reviewed').length;
  const needsReviewCount = tenders.filter(t => t.status === 'needs_review').length;
  const visibleTenders = statusFilter === 'all' ? tenders : tenders.filter(t => t.status === statusFilter);

  if (loading) return <div className="loading">Loading…</div>;
  if (error) return <div className="error">{error}</div>;

  return (
    <div>
      <h1 style={{ fontSize: 28, fontWeight: 700, marginBottom: 4 }}>Review Queue</h1>
      <p style={{ color: '#8892a4', marginBottom: 16 }}>Triage scored matches — confirm relevance before they reach the analyst's shortlist</p>

      {/* CR-007 Phase B (B2): pills are now clickable filters, so "needs
          further review" has the CR's own "filter/section so parked tenders
          don't get lost" without new UI chrome. */}
      <div style={{ display: 'flex', gap: 10, marginBottom: 20, alignItems: 'center' }}>
        <button
          className="pill pill-grey" style={{ cursor: 'pointer', border: statusFilter === 'all' ? '1px solid #4c5a70' : undefined }}
          onClick={() => setStatusFilter('all')}
        >
          All ({tenders.length})
        </button>
        <button
          className="pill pill-grey" style={{ cursor: 'pointer', border: statusFilter === 'new' ? '1px solid #8892a4' : undefined }}
          onClick={() => setStatusFilter(f => f === 'new' ? 'all' : 'new')}
        >
          ○ {newCount} new
        </button>
        <button
          className="pill pill-green" style={{ cursor: 'pointer', border: statusFilter === 'shortlisted' ? '1px solid #34d399' : undefined }}
          onClick={() => setStatusFilter(f => f === 'shortlisted' ? 'all' : 'shortlisted')}
        >
          ● {shortlistedCount} shortlisted
        </button>
        <button
          className="pill pill-amber" style={{ cursor: 'pointer', border: statusFilter === 'reviewed' ? '1px solid #e3b341' : undefined }}
          onClick={() => setStatusFilter(f => f === 'reviewed' ? 'all' : 'reviewed')}
        >
          ● {reviewedCount} reviewed
        </button>
        <button
          className="pill pill-purple" style={{ cursor: 'pointer', border: statusFilter === 'needs_review' ? '1px solid #c084fc' : undefined }}
          onClick={() => setStatusFilter(f => f === 'needs_review' ? 'all' : 'needs_review')}
        >
          ● {needsReviewCount} needs review
        </button>
        <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ color: '#8892a4', fontSize: 12 }}>Sort by</span>
          <select
            value={sortBy}
            onChange={e => setSortBy(e.target.value as SortBy)}
            style={{ background: '#151d2c', border: '1px solid #1a2334', color: '#e2e8f0', padding: '5px 10px', borderRadius: 6, fontSize: 13, cursor: 'pointer', outline: 'none' }}
          >
            <option value="pub_date">Release date (newest first)</option>
            <option value="deadline">Deadline</option>
          </select>
        </div>
      </div>

      {tenders.length === 0 ? (
        <div className="card" style={{ padding: 32, textAlign: 'center', color: '#8892a4' }}>No tenders to review.</div>
      ) : visibleTenders.length === 0 ? (
        <div className="card" style={{ padding: 32, textAlign: 'center', color: '#8892a4' }}>
          No tenders match this filter. <button className="btn btn-ghost" style={{ fontSize: 12 }} onClick={() => setStatusFilter('all')}>Clear filter</button>
        </div>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, alignItems: 'flex-start' }}>
          {/* Left list */}
          <div className="card" style={{ maxHeight: '75vh', overflowY: 'auto' }}>
            <div style={{ padding: '12px 16px', borderBottom: '1px solid #1a2334', fontSize: 11, fontWeight: 700, letterSpacing: '0.08em', color: '#8892a4', textTransform: 'uppercase', position: 'sticky', top: 0, background: '#151d2c', zIndex: 1 }}>
              Queue · {visibleTenders.length} matches
            </div>
            {visibleTenders.map(t => {
              const conf = confidenceFromMatchSource(t.match_source);
              const isActive = selected?.pub_number === t.pub_number;
              const dotColor = statusDotColor(t.status);
              const barColor = conf >= 80 ? '#2EE6D4' : '#e3b341';
              const translated = hasTranslatedTagLine(t);
              // CR-007 Phase B (B1): a soft-dismissed row greys out in place
              // rather than disappearing (see load()'s filter above).
              const softDismissed = t.status === 'dismissed';
              // CR-007 Phase D (D2): advisory only — never filters the row out.
              const hrsLeft = hoursLeft(t.deadline);
              const closingSoon = hrsLeft !== null && hrsLeft >= 0 && hrsLeft <= deadlineFloorHours;
              return (
                <div
                  key={t.pub_number}
                  onClick={() => selectTender(t)}
                  style={{
                    padding: '12px 14px', cursor: 'pointer', borderBottom: '1px solid #1a2334',
                    background: isActive ? 'rgba(46,230,212,0.05)' : 'transparent',
                    borderLeft: `3px solid ${isActive ? '#2EE6D4' : 'transparent'}`,
                    opacity: softDismissed ? 0.5 : 1,
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8 }}>
                    <div style={{ width: 10, height: 10, borderRadius: '50%', border: `2px solid ${dotColor}`, background: t.status !== 'new' ? dotColor : 'transparent', marginTop: 3, flexShrink: 0 }} />
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div
                        title={translated ? `Original: ${t.tag_line}` : undefined}
                        style={{ fontWeight: 500, fontSize: 13, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: isActive ? '#e2e8f0' : '#c8d0de' }}
                      >
                        {translated && <span title="Translated from source language" style={{ marginRight: 4 }}>🌐</span>}
                        {displayTagLine(t, false)}
                      </div>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 2 }}>
                        <span style={{ color: '#8892a4', fontSize: 11 }}>{t.source} · {t.country}</span>
                        <NoticeTypeBadge noticeType={t.notice_type} />
                        {closingSoon && (
                          <span title={`Due within ${deadlineFloorHours}h`} style={{ color: '#f87171', fontSize: 10, fontWeight: 700, letterSpacing: '0.04em', textTransform: 'uppercase' }}>
                            ⏱ Closing soon
                          </span>
                        )}
                        {t.viewers.length > 0 && (
                          <span title={`${t.viewers.map(v => v.account_name).join(', ')} currently working this`} style={{ fontSize: 11 }}>👀</span>
                        )}
                        {t.duplicates.length > 0 && (
                          <span title={t.duplicates.some(d => d.match_type === 'same_source')
                            ? 'Possible duplicate of a tender already in review'
                            : `Also listed on ${t.duplicates.map(d => d.source).join(', ')}`} style={{ fontSize: 11 }}>🔗</span>
                        )}
                        {t.status === 'needs_review' && t.assigned_to && (
                          <span title={`Assigned to ${t.assigned_to}`} style={{ fontSize: 11 }}>✉</span>
                        )}
                      </div>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 6 }}>
                        <div style={{ flex: 1, height: 3, background: '#1a2334', borderRadius: 9999, overflow: 'hidden' }}>
                          <div style={{ width: `${conf}%`, height: '100%', background: barColor, borderRadius: 9999 }} />
                        </div>
                        <span style={{ color: '#8892a4', fontSize: 11, whiteSpace: 'nowrap' }}>{conf}%</span>
                      </div>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>

          {/* Right detail */}
          {selected && (
            <div className="card" style={{ maxHeight: '75vh', overflowY: 'auto' }}>
              <div style={{ padding: '16px 20px' }}>
                {/* Header */}
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12, flexWrap: 'wrap' }}>
                  <span style={{ background: '#1a2334', color: '#e2e8f0', padding: '2px 8px', borderRadius: 4, fontSize: 12, fontWeight: 600 }}>{selected.source}</span>
                  <span style={{ fontSize: 16 }}>{countryFlag(selected.country)}</span>
                  <span style={{ color: '#8892a4', fontSize: 13 }}>{selected.country}</span>
                  <NoticeTypeBadge noticeType={selected.notice_type} />
                  <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 10 }}>
                    {selected.url && (
                      <a href={selected.url} target="_blank" rel="noopener noreferrer"
                         className="btn btn-ghost" style={{ fontSize: 12, padding: '4px 10px' }}>
                        Open ↗
                      </a>
                    )}
                    <StatusBadge status={selected.status} />
                  </div>
                </div>

                {/* CR-007 Phase D (D1): collaboration warning — presence only,
                    never blocks the triage actions below (see api._attach_presence). */}
                {selected.viewers.length > 0 && (
                  <div style={{ marginBottom: 16, padding: 10, background: 'rgba(227,179,65,0.08)', border: '1px solid rgba(227,179,65,0.25)', borderRadius: 8, display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span style={{ fontSize: 12 }}>👀</span>
                    <span style={{ fontSize: 12, color: '#e3b341' }}>
                      {selected.viewers.map(v => v.account_name).join(', ')}
                      {selected.viewers.length === 1 ? ' is' : ' are'} currently working this tender
                    </span>
                  </div>
                )}

                {/* CR-007 Phase C: cross-portal duplicate — surfaced on both
                    records (never hides either), with a direct link to the
                    counterpart's own portal (C2, falls out of storing `url`
                    on each side already — see api._attach_duplicates). */}
                {selected.duplicates.length > 0 && (
                  <div style={{ marginBottom: 16, padding: 10, background: 'rgba(96,165,250,0.05)', border: '1px solid rgba(96,165,250,0.2)', borderRadius: 8, display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center' }}>
                    <span style={{ color: '#60a5fa', fontSize: 12 }}>🔗</span>
                    {selected.duplicates.map(d => (
                      <span key={d.pub_number} style={{ fontSize: 12, color: '#c8d0de' }}>
                        {d.match_type === 'same_source' ? (
                          <>Possible duplicate — similar tender already in review ({d.pub_number})</>
                        ) : (
                          <>Also listed on <strong>{d.source}</strong>
                          {d.match_type === 'similarity' && d.similarity != null && ` (${Math.round(d.similarity * 100)}% match)`}</>
                        )}
                        {d.url && (
                          <> · <a href={d.url} target="_blank" rel="noopener noreferrer" style={{ color: '#60a5fa' }}>View ↗</a></>
                        )}
                      </span>
                    ))}
                  </div>
                )}

                <h2 style={{ fontSize: 20, fontWeight: 700, marginBottom: needsTranslation(selected) ? 6 : 20, lineHeight: 1.3 }}>
                  {displayTagLine(selected, showOriginal)}
                </h2>

                {needsTranslation(selected) && (
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 20, flexWrap: 'wrap' }}>
                    {hasTranslatedTagLine(selected) ? (
                      <>
                        <span style={{ color: '#8892a4', fontSize: 12 }}>
                          🌐 {showOriginal ? `Original (${selected.language})` : 'Translated to English'}
                        </span>
                        <button className="btn btn-ghost" style={{ fontSize: 11, padding: '2px 8px' }}
                                onClick={() => setShowOriginal(o => !o)}>
                          {showOriginal ? 'Show translation' : 'Show original'}
                        </button>
                        {/* CR-005 follow-up: the title and description are translated by two
                            independent DeepL calls — one can succeed while the other doesn't
                            (e.g. a quota that covers the short title but not the longer body).
                            Surface that rather than silently showing an untranslated body under
                            a banner that claims everything's translated. */}
                        {!showOriginal && !hasTranslatedDescription(selected) && (
                          <span style={{ color: '#e3b341', fontSize: 12, background: 'rgba(227,179,65,0.1)', border: '1px solid rgba(227,179,65,0.3)', borderRadius: 4, padding: '2px 8px' }}>
                            ⚠ Description translation unavailable — showing original
                          </span>
                        )}
                      </>
                    ) : (
                      <span style={{ color: '#e3b341', fontSize: 12, background: 'rgba(227,179,65,0.1)', border: '1px solid rgba(227,179,65,0.3)', borderRadius: 4, padding: '2px 8px' }}>
                        ⚠ Translation unavailable — showing original ({selected.language})
                      </span>
                    )}
                  </div>
                )}

                {selected.description && (
                  <p style={{ color: '#c8d0de', fontSize: 13, lineHeight: 1.6, marginBottom: 20 }}>
                    {displayDescription(selected, showOriginal)}
                  </p>
                )}

                {/* Core elements */}
                <div style={{ marginBottom: 20 }}>
                  <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.08em', color: '#4c5a70', textTransform: 'uppercase', marginBottom: 12 }}>Core Elements</div>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px 20px' }}>
                    <div>
                      <div style={{ fontSize: 10, fontWeight: 700, color: '#4c5a70', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 3 }}>Issuing Authority</div>
                      <div style={{ fontSize: 13 }}>{selected.buyer || '—'}</div>
                    </div>
                    <div>
                      <div style={{ fontSize: 10, fontWeight: 700, color: '#4c5a70', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 3 }}>Country</div>
                      <div style={{ fontSize: 13 }}>{countryFlag(selected.country)} {selected.country}</div>
                    </div>
                    <div>
                      <div style={{ fontSize: 10, fontWeight: 700, color: '#4c5a70', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 3 }}>Region</div>
                      <div style={{ fontSize: 13 }}>{selected.place || '—'}</div>
                    </div>
                    <div>
                      <div style={{ fontSize: 10, fontWeight: 700, color: '#4c5a70', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 3 }}>Source Portal</div>
                      <div style={{ fontSize: 13 }}>{selected.source}</div>
                    </div>
                    <div>
                      <div style={{ fontSize: 10, fontWeight: 700, color: '#4c5a70', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 3 }}>Procedure Type</div>
                      <div style={{ fontSize: 13 }}>{selected.procedure || '—'}</div>
                    </div>
                    <div>
                      <div style={{ fontSize: 10, fontWeight: 700, color: '#4c5a70', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 3 }}>Deadline</div>
                      <div className="mono" style={{ fontSize: 13 }}>{formatDate(selected.deadline)}</div>
                    </div>
                    {/* CR-002 C4: omit entirely when the source notice discloses no value — never show a placeholder */}
                    {formatValue(selected.value, selected.value_currency) && (
                      <div>
                        <div style={{ fontSize: 10, fontWeight: 700, color: '#4c5a70', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 3 }}>Est. Value</div>
                        <div className="mono" style={{ fontSize: 13 }}>{formatValue(selected.value, selected.value_currency)}</div>
                      </div>
                    )}
                  </div>

                  {selected.cpv_codes?.length > 0 && (
                    <div style={{ marginTop: 12 }}>
                      <div style={{ fontSize: 10, fontWeight: 700, color: '#4c5a70', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 6 }}>CPV Codes</div>
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                        {/* stable order, unique only — the engine dedupes at ingest, this is a defensive backstop */}
                        {Array.from(new Set(selected.cpv_codes)).map(c => (
                          <span key={c} className="mono" style={{ background: 'rgba(52,211,153,0.1)', color: '#34d399', border: '1px solid rgba(52,211,153,0.25)', padding: '2px 8px', borderRadius: 4, fontSize: 12 }}>{c}</span>
                        ))}
                      </div>
                    </div>
                  )}

                  {selected.matched_terms?.length > 0 && (
                    <div style={{ marginTop: 12 }}>
                      <div style={{ fontSize: 10, fontWeight: 700, color: '#4c5a70', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 6 }}>Matched Terms</div>
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                        {selected.matched_terms.map(term => (
                          <span key={term} style={{ background: 'rgba(96,165,250,0.1)', color: '#60a5fa', border: '1px solid rgba(96,165,250,0.25)', padding: '2px 8px', borderRadius: 4, fontSize: 12 }}>{term}</span>
                        ))}
                      </div>
                    </div>
                  )}
                </div>

                {/* Confidence & Signals */}
                <div style={{ marginBottom: 20 }}>
                  <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.08em', color: '#4c5a70', textTransform: 'uppercase', marginBottom: 10 }}>Confidence & Signals</div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 8 }}>
                    <MatchChip matchSource={selected.match_source} />
                    <span style={{ color: '#8892a4', fontSize: 12 }}>{confidenceLabel(selected.match_source)}</span>
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <div style={{ flex: 1, height: 4, background: '#1a2334', borderRadius: 9999, overflow: 'hidden' }}>
                      <div style={{
                        width: `${confidenceFromMatchSource(selected.match_source)}%`,
                        height: '100%',
                        background: confidenceFromMatchSource(selected.match_source) >= 80 ? '#2EE6D4' : '#e3b341',
                        borderRadius: 9999,
                      }} />
                    </div>
                    <span style={{ color: '#e2e8f0', fontSize: 13, fontWeight: 600, minWidth: 36 }}>
                      {confidenceFromMatchSource(selected.match_source)}%
                    </span>
                  </div>
                </div>

                {/* CR-007 Phase B (B3): relevance — only present while the
                    tender is still undecided (new/needs_review), see
                    api._attach_relevance. Explainable by construction: the
                    reasoning text names the actual similar-tender counts
                    behind the score, not a black-box model. */}
                {selected.relevance_score !== undefined && (
                  <div style={{ marginBottom: 20 }}>
                    <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.08em', color: '#4c5a70', textTransform: 'uppercase', marginBottom: 10 }}>
                      Relevance {selected.relevance_corrected && <span style={{ color: '#c084fc', textTransform: 'none', fontWeight: 500 }}>· corrected{selected.relevance_corrected_by ? ` by ${selected.relevance_corrected_by}` : ''}</span>}
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                      <div style={{ flex: 1, height: 4, background: '#1a2334', borderRadius: 9999, overflow: 'hidden' }}>
                        <div style={{
                          width: `${selected.relevance_score}%`, height: '100%',
                          background: selected.relevance_score >= 60 ? '#34d399' : selected.relevance_score >= 40 ? '#e3b341' : '#f87171',
                          borderRadius: 9999,
                        }} />
                      </div>
                      <span style={{ color: '#e2e8f0', fontSize: 13, fontWeight: 600, minWidth: 36 }}>
                        {selected.relevance_score}%
                      </span>
                    </div>
                    <div style={{ color: '#8892a4', fontSize: 12, marginBottom: 8 }}>{selected.relevance_reasoning}</div>
                    {!relevanceEditing ? (
                      <button className="btn btn-ghost" disabled={patching} onClick={startRelevanceCorrection} style={{ fontSize: 11, padding: '3px 8px' }}>
                        Correct this
                      </button>
                    ) : (
                      <div style={{ padding: 10, background: 'rgba(192,132,252,0.05)', border: '1px solid rgba(192,132,252,0.2)', borderRadius: 8 }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                          <input
                            type="number" min={0} max={100} className="input-field" style={{ width: 70 }}
                            value={relevanceScoreInput} onChange={e => setRelevanceScoreInput(e.target.value)}
                          />
                          <span style={{ color: '#8892a4', fontSize: 12 }}>%</span>
                        </div>
                        <textarea
                          className="input-field" style={{ minHeight: 44, resize: 'vertical', width: '100%' }}
                          placeholder="Why? (optional)" value={relevanceNoteInput}
                          onChange={e => setRelevanceNoteInput(e.target.value)}
                        />
                        <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
                          <button className="btn" disabled={patching} onClick={submitRelevanceCorrection}
                                  style={{ background: '#c084fc', color: '#0f1623', fontWeight: 600, fontSize: 12 }}>
                            Save correction
                          </button>
                          <button className="btn btn-ghost" disabled={patching} onClick={() => setRelevanceEditing(false)} style={{ fontSize: 12 }}>
                            Cancel
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                )}

                {/* Triage buttons */}
                <div style={{ borderTop: '1px solid #1a2334', paddingTop: 16, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                  <button
                    className="btn"
                    disabled={patching}
                    onClick={() => applyStatus('shortlisted')}
                    style={{
                      background: selected.status === 'shortlisted' ? '#34d399' : 'rgba(52,211,153,0.1)',
                      color: selected.status === 'shortlisted' ? '#0f1623' : '#34d399',
                      border: '1px solid rgba(52,211,153,0.3)',
                      fontWeight: 600,
                    }}
                  >
                    ✓ Shortlist
                  </button>
                  <button
                    className="btn"
                    disabled={patching}
                    onClick={() => applyStatus('reviewed')}
                    style={{
                      background: selected.status === 'reviewed' ? '#e3b341' : 'rgba(227,179,65,0.1)',
                      color: selected.status === 'reviewed' ? '#0f1623' : '#e3b341',
                      border: '1px solid rgba(227,179,65,0.3)',
                      fontWeight: 600,
                    }}
                  >
                    Mark reviewed
                  </button>
                  <button
                    className="btn"
                    disabled={patching || selected.status === 'needs_review'}
                    onClick={startNeedsReview}
                    style={{
                      background: selected.status === 'needs_review' ? '#c084fc' : 'rgba(192,132,252,0.1)',
                      color: selected.status === 'needs_review' ? '#0f1623' : '#c084fc',
                      border: '1px solid rgba(192,132,252,0.3)',
                      fontWeight: 600,
                    }}
                  >
                    Needs further review
                  </button>
                  {/* CR-007 B1: "Dismiss" starts stage 1 (or is disabled once
                      already at either dismiss stage); "Dismiss permanently"
                      only appears once stage 1 is reached, to progress to
                      stage 2 rather than restarting the note. */}
                  <button
                    className="btn"
                    disabled={patching || selected.status === 'dismissed' || selected.status === 'dismissed_final'}
                    onClick={startDismiss}
                    style={{
                      background: selected.status === 'dismissed_final' ? '#f87171' : 'rgba(248,113,113,0.1)',
                      color: selected.status === 'dismissed_final' ? '#0f1623' : '#f87171',
                      border: '1px solid rgba(248,113,113,0.3)',
                      fontWeight: 600,
                    }}
                  >
                    Dismiss
                  </button>
                  {selected.status === 'dismissed' && (
                    <button
                      className="btn"
                      disabled={patching}
                      onClick={startDismiss}
                      style={{ background: '#f87171', color: '#0f1623', border: '1px solid rgba(248,113,113,0.3)', fontWeight: 600 }}
                    >
                      Dismiss permanently
                    </button>
                  )}
                  {selected.status !== 'new' && (
                    <button
                      className="btn btn-ghost"
                      disabled={patching}
                      onClick={() => applyStatus('new')}
                      style={{ fontSize: 12 }}
                    >
                      Reset to new
                    </button>
                  )}
                </div>

                {/* CR-007 Phase G (G1): "please review this tender" — an
                    explicit account-to-account nudge, separate from the
                    triage actions above (doesn't change status). */}
                <ForwardTender pubNumber={selected.pub_number} />

                {/* CR-006 D2 / CR-007 B1-B2: reason required — Confirm stays
                    disabled until the note (and, for a dismiss, category) is set. */}
                {noteAction && (
                  <div style={{ marginTop: 12, padding: 12, background: 'rgba(248,113,113,0.05)', border: '1px solid rgba(248,113,113,0.2)', borderRadius: 8 }}>
                    <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.08em', color: '#4c5a70', textTransform: 'uppercase', marginBottom: 8 }}>
                      {noteAction.label} — reason (required)
                    </div>
                    <textarea
                      className="input-field"
                      style={{ minHeight: 60, resize: 'vertical', width: '100%' }}
                      placeholder={noteAction.requireCategory ? 'Why is this being dismissed?' : 'Why is this parked for further review?'}
                      value={noteText}
                      onChange={e => setNoteText(e.target.value)}
                    />
                    {noteAction.requireCategory && (
                      <select
                        className="input-field"
                        style={{ marginTop: 8, width: '100%' }}
                        value={reasonCategory}
                        onChange={e => setReasonCategory(e.target.value)}
                      >
                        <option value="">Category (required)…</option>
                        {REASON_CATEGORIES.map(c => <option key={c.value} value={c.value}>{c.label}</option>)}
                      </select>
                    )}
                    {noteAction.allowAssignee && !assigneeManual && (
                      <select
                        className="input-field"
                        style={{ marginTop: 8, width: '100%' }}
                        value={assignedTo}
                        onChange={e => {
                          if (e.target.value === MANUAL_ASSIGNEE) { setAssigneeManual(true); setAssignedTo(''); }
                          else setAssignedTo(e.target.value);
                        }}
                      >
                        <option value="">Assign to colleague (optional)…</option>
                        {orgMembers.map(m => <option key={m.clerk_user_id} value={m.email}>{m.email}</option>)}
                        <option value={MANUAL_ASSIGNEE}>Other (enter email)…</option>
                      </select>
                    )}
                    {noteAction.allowAssignee && assigneeManual && (
                      <>
                        <input
                          className="input-field"
                          style={{ marginTop: 8, width: '100%' }}
                          placeholder="Assign to colleague's email (optional)…"
                          value={assignedTo}
                          onChange={e => setAssignedTo(e.target.value)}
                        />
                        {orgMembers.length > 0 && (
                          <button className="btn btn-ghost" style={{ fontSize: 11, padding: '2px 6px', marginTop: 4 }}
                                  onClick={() => { setAssigneeManual(false); setAssignedTo(''); }}>
                            ← Pick from colleagues
                          </button>
                        )}
                      </>
                    )}
                    <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
                      <button className="btn"
                              disabled={patching || !noteText.trim() || (noteAction.requireCategory && !reasonCategory)
                                || (assignedTo.trim() !== '' && !assignedTo.includes('@'))}
                              onClick={confirmNoteAction}
                              style={{ background: '#f87171', color: '#0f1623', fontWeight: 600, fontSize: 12 }}>
                        {noteAction.label}
                      </button>
                      <button className="btn btn-ghost" disabled={patching}
                              onClick={closeNoteAction}
                              style={{ fontSize: 12 }}>
                        Cancel
                      </button>
                    </div>
                  </div>
                )}

                {(selected.status === 'dismissed' || selected.status === 'dismissed_final' || selected.status === 'needs_review')
                  && selected.dismissal_reason && (
                  <div style={{ marginTop: 12, padding: 12, background: 'rgba(248,113,113,0.05)', border: '1px solid rgba(248,113,113,0.2)', borderRadius: 8 }}>
                    <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.08em', color: '#4c5a70', textTransform: 'uppercase', marginBottom: 6 }}>
                      {selected.status === 'needs_review' ? 'Review comment' : 'Dismissal reason'}
                    </div>
                    <div style={{ fontSize: 13, color: '#c8d0de' }}>{selected.dismissal_reason}</div>
                    {selected.dismissal_reason_category && (
                      <div style={{ fontSize: 11, color: '#8892a4', marginTop: 4 }}>
                        Category: {REASON_CATEGORIES.find(c => c.value === selected.dismissal_reason_category)?.label ?? selected.dismissal_reason_category}
                      </div>
                    )}
                    {(selected.dismissed_by || selected.dismissed_at) && (
                      <div style={{ fontSize: 11, color: '#8892a4', marginTop: 6 }}>
                        {selected.dismissed_by && <>by {selected.dismissed_by}</>}
                        {selected.dismissed_by && selected.dismissed_at && ' · '}
                        {selected.dismissed_at && formatDate(selected.dismissed_at)}
                      </div>
                    )}
                    {selected.status === 'needs_review' && selected.assigned_to && (
                      <div style={{ fontSize: 11, color: '#c084fc', marginTop: 6 }}>
                        ✉ Assigned to {selected.assigned_to}
                      </div>
                    )}
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
