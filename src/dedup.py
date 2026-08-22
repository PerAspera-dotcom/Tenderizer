"""Cross-record dedup pass (CR-001 D-DUP) — collapses republished/updated
notices that get a new pub_number, which store.py's exact-hash dedup
(sha256(source|pub_number)) never catches since the pub_number differs.

This is a post-processing pass over the FULL record set, not a per-record
filter like filters.py — it inherently needs multiple records to compare, so
it runs once per pipeline execution after ingestion (see run.py), not inline
per record.

Match rule: same buyer + same-or-close deadline (±7 days — the CR gives no
exact tolerance; a republish commonly also amends/extends the deadline, and
title+buyer both having to agree tightly keeps false-merge risk low) + title
similarity >= TITLE_SIMILARITY_FLOOR (0.9, per the CR). All three must hold.
"""
import re
from collections import defaultdict
from datetime import date, timedelta
from difflib import SequenceMatcher
from unidecode import unidecode

TITLE_SIMILARITY_FLOOR = 0.9
DEADLINE_WINDOW = timedelta(days=7)

# CR-007 Phase C: fallback default for find_cross_portal_duplicates' signal-2
# threshold when no tenant override exists (see store.get_dedup_settings /
# tenant_dedup_settings) — kept as a module constant for the same reason
# TITLE_SIMILARITY_FLOOR is, and for any caller (e.g. a test) that wants the
# function's own default rather than threading a tenant setting through.
DESCRIPTION_SIMILARITY_DEFAULT = 0.75


def _normalize(text):
    text = unidecode(text or "").lower()
    return re.sub(r"\s+", " ", text).strip()


def _title_similarity(a, b):
    return SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


def _parse_date(deadline):
    try:
        return date.fromisoformat((deadline or "")[:10])
    except ValueError:
        return None


def _same_or_close_deadline(a, b):
    da, db = _parse_date(a.get("deadline")), _parse_date(b.get("deadline"))
    if da is None or db is None:
        return False
    return abs(da - db) <= DEADLINE_WINDOW


def kept_order_is_confident(kept, candidate):
    """CR-008 P0 follow-up: true only when `kept`'s pub_date is a real,
    strictly-later date than `candidate`'s — i.e. we actually know `kept` is
    the newer republish, not just whichever record happened to sort first.

    find_duplicate_groups' "keep index 0" pick relies on sorting by pub_date
    descending, but TED records commonly carry pub_date == "" (confirmed via
    a live production incident: tender 563438-2026 and its genuine republish
    547299-2026 both had pub_date == "" — same buyer, same title, deadlines
    3 days apart, TED's real amended-deadline-republish pattern). When every
    candidate in a group ties (most commonly because they're all blank),
    Python's stable sort just preserves whatever order store.all_records
    happened to return, which is unspecified per the DB, and confirmed to
    differ ACROSS TENANTS for the exact same public TED notice pair — the
    same notice pair resolved to a different "kept" survivor in different
    tenants purely from row order. That's not a real "newest wins" decision,
    it's a coin flip, and a coin flip must never decide what gets hidden.

    A candidate that fails this check is never superseded — it's surfaced
    as a same-source possible-duplicate link instead (see run.py), same as
    an is_protected candidate.
    """
    kd, cd = kept.get("pub_date") or "", candidate.get("pub_date") or ""
    return bool(kd) and bool(cd) and kd > cd


def is_protected(record):
    """CR-008 P0: true once a human has touched this tender — any status past
    the pipeline's own 'new' default, or a set assigned_to/reason_category
    (both of which only ever get set alongside a status transition via
    store.set_status, but checked independently here in case that ever
    changes). A protected record must never be auto-superseded by
    find_duplicate_groups/mark_superseded (see run.py's dedup pass) — the
    reported incident (tender 563438-2026 vanishing from the Review Queue)
    traced back to an unconditional supersede colliding with a tender someone
    was actively reviewing. Client instruction: "too many tenders rather than
    too few" — a protected candidate gets surfaced as a possible duplicate
    (store.upsert_tender_duplicate, match_type='same_source') instead of
    hidden.
    """
    if (record.get("status") or "new") != "new":
        return True
    return bool(record.get("assigned_to")) or bool(record.get("reason_category"))


def find_duplicate_groups(records):
    """Group records that are republished versions of the same tender.

    Returns a list of groups; each group is a list of >=2 records sorted by
    pub_date descending (index 0 is the one to keep). Already-excluded records
    are never considered (a superseded record shouldn't itself gain followers),
    and each record appears in at most one group.
    """
    candidates = [r for r in records if not r.get("exclude_reason")]
    by_buyer = defaultdict(list)
    for r in candidates:
        key = _normalize(r.get("buyer"))
        if key:
            by_buyer[key].append(r)

    groups = []
    for same_buyer_recs in by_buyer.values():
        if len(same_buyer_recs) < 2:
            continue
        seen = set()
        for i, a in enumerate(same_buyer_recs):
            if a["pub_number"] in seen:
                continue
            group = [a]
            for b in same_buyer_recs[i + 1:]:
                if b["pub_number"] in seen:
                    continue
                if (_same_or_close_deadline(a, b)
                        and _title_similarity(a.get("tag_line"), b.get("tag_line")) >= TITLE_SIMILARITY_FLOOR):
                    group.append(b)
            if len(group) > 1:
                seen.update(r["pub_number"] for r in group)
                group.sort(key=lambda r: r.get("pub_date") or "", reverse=True)
                groups.append(group)
    return groups


def find_cross_portal_duplicates(records, similarity_threshold=DESCRIPTION_SIMILARITY_DEFAULT):
    """CR-007 Phase C — cross-portal duplicate detection (e.g. the same
    tender on both TED and a national portal like BOAMP). Two independent
    signals, either one is enough:
      1. Reference match — both records carry the same (normalized) buyer-
         assigned `internal_identifier` (see normalize.py's normalize_ted /
         _boamp_internal_identifier — neither portal exposes a literal link
         to the other, confirmed via a live field-catalog probe of both
         APIs; the buyer's own procurement reference is the real join key).
         Exact match, no threshold.
      2. Description similarity — translated descriptions (`description_en`,
         only populated post-translation) above `similarity_threshold`, via
         the same SequenceMatcher technique find_duplicate_groups above uses
         for titles.
    Unlike find_duplicate_groups, this never merges/hides anything — it only
    reports pairs for the caller (store.upsert_tender_duplicate) to record
    and surface on *both* records (api._attach_duplicates), per the CR:
    "Don't silently delete either — surface the link and let the reviewer
    act." Only cross-source pairs are considered (same-source republishes
    are find_duplicate_groups' job); already-excluded records never
    participate, same reasoning as find_duplicate_groups.

    Deliberately does NOT bucket by country: TED returns ISO3 buyer-country
    codes ("FRA") while normalize_boamp hardcodes ISO2 ("FR") — comparing
    them directly would silently never match the exact TED-France/BOAMP
    pairing this feature exists for. CPV-code sharing is the search-space
    filter instead.

    Returns a list of (pub_number_a, pub_number_b, match_type, similarity)
    tuples, `pub_number_a < pub_number_b`, each pair appearing at most once
    even if both signals would have flagged it (reference wins).
    """
    candidates = [r for r in records if not r.get("exclude_reason")]
    matches = []
    seen_pairs = set()

    def _add(pub_a, pub_b, match_type, similarity=None):
        key = tuple(sorted((pub_a, pub_b)))
        if key in seen_pairs:
            return
        seen_pairs.add(key)
        matches.append((key[0], key[1], match_type, similarity))

    by_reference = defaultdict(list)
    for r in candidates:
        ref = (r.get("internal_identifier") or "").strip().upper()
        if ref:
            by_reference[ref].append(r)
    for group in by_reference.values():
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                if a["source"] != b["source"]:
                    _add(a["pub_number"], b["pub_number"], "reference")

    by_cpv = defaultdict(list)
    for r in candidates:
        for cpv in r.get("cpv_codes") or []:
            by_cpv[cpv].append(r)
    for group in by_cpv.values():
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                if a["source"] == b["source"]:
                    continue
                key = tuple(sorted((a["pub_number"], b["pub_number"])))
                if key in seen_pairs:
                    continue  # already linked by the reference signal
                desc_a, desc_b = a.get("description_en"), b.get("description_en")
                if not desc_a or not desc_b:
                    continue
                sim = SequenceMatcher(None, _normalize(desc_a), _normalize(desc_b)).ratio()
                if sim >= similarity_threshold:
                    _add(a["pub_number"], b["pub_number"], "similarity", round(sim, 4))

    return matches
