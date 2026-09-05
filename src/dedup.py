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


def find_duplicate_groups(records):
    """Group records that are republished versions of the same tender.

    Returns a list of groups; each group is a list of >=2 records. Already-
    excluded records are never considered, and each record appears in at
    most one group.

    CR-008 P0 (simplified 2026-08-26, client direction): this used to also
    pick which record in a group to auto-supersede (sorted by pub_date
    descending, index 0 kept), guarded by dedup.is_protected/
    kept_order_is_confident so an in-review tender or an unreliable
    (commonly blank on TED) pub_date could never get silently hidden. That
    guarded-auto-merge machinery is retired: every group's members now just
    get linked as a same-source possible-duplicate (see run.py) — the same
    "surface, never hide" rule CR-007 Phase C already uses for cross-portal
    matches. A plain label the reviewer acts on is simpler than gating who's
    safe to auto-merge, and can't silently hide a tender at all, guarded or
    not — the standing client instruction throughout this dedup work has
    been "too many tenders rather than too few." pub_date sort is kept only
    to give the same-source link a stable, human-readable ordering (which
    side reads as "kept" in the UI), not to decide anything.
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


# ── Duplicate-resolution follow-up (2026-08-26) ──────────────────────────────
# find_duplicate_groups above is the detection pass (runs once per pipeline
# run, decides WHICH records are same_source matches). Everything below is
# read-side: given the tender_duplicates rows detection already wrote,
# reconstruct group membership/ordering for display and for the Review
# Queue's "unique only" filter. Never runs as part of the pipeline, never
# writes anything — pure functions over already-detected pairs.


def _order_key(rec):
    """(basis, value) for chronological ordering — pub_date when it's
    actually populated, first_seen otherwise. TED commonly ships a blank
    pub_date (confirmed live during the CR-008 P0 investigation); first_seen
    is always populated (store.upsert stamps it at insert time for every
    record, both sources) but means "when our pipeline first saw it," not
    "when it was published" — hence the two are never mixed across sides of
    a comparison (see _group_ordering).
    """
    pub_date = (rec.get("pub_date") or "")[:10]
    if pub_date:
        return ("pub_date", pub_date)
    first_seen = (rec.get("first_seen") or "")[:10]
    if first_seen:
        return ("first_seen", first_seen)
    return (None, None)


def _group_ordering(members):
    """Ascending-chronological [(key, member), ...] for a group, or None if
    order can't be honestly established.

    Deliberately all-or-nothing: if any two members tie on their resolved
    date value, the whole group's order is reported unknown rather than
    ranking the unambiguous members and leaving the tied ones unranked — a
    partial ranking reads as more confident than the data supports. Members
    with no resolvable date at all (shouldn't happen; first_seen is always
    populated) also force "unknown" rather than being silently dropped from
    the ranking.
    """
    keyed = [(_order_key(m), m) for m in members]
    if any(k == (None, None) for k, _ in keyed):
        return None
    # All members must resolve via the SAME basis — comparing one member's
    # real publish date against another's mere discovery date isn't a
    # meaningful comparison, even though both are non-null.
    if len({k[0] for k, _ in keyed}) > 1:
        return None
    values = [k[1] for k, _ in keyed]
    if len(set(values)) < len(values):
        return None
    return sorted(keyed, key=lambda km: (km[0][1], km[1]["pub_number"]))


def _pick_canonical(members):
    """The representative record for a group, for the Review Queue's
    "unique only" filter. Cascade, most-specific signal first:

      1. If only some members carry a deadline, restrict to those — a
         tender with no deadline is close to unusable in this queue no
         matter how it otherwise compares.
      2. Newest by _group_ordering (mirrors the retired dedup.
         find_duplicate_groups "kept" convention: a republish is usually a
         deadline amendment/correction, so the newest version is the one a
         reviewer should act on, not the historical original).
      3. If order is unknown and only some members have already been
         triaged (status != 'new'), restrict to those — preserves a
         reviewer's existing judgment over an arbitrary pick.
      4. Deterministic tie-break: lowest pub_number. Never row order — a
         tenant-dependent, non-deterministic "kept" pick is the exact bug
         CR-008 P0 was filed over.

    Returns a pub_number, not a record, so callers don't need to keep the
    member dicts around afterward.
    """
    pool = members
    with_deadline = [m for m in pool if (m.get("deadline") or "").strip()]
    if with_deadline and len(with_deadline) < len(pool):
        pool = with_deadline
    if len(pool) > 1:
        ordering = _group_ordering(pool)
        if ordering:
            return ordering[-1][1]["pub_number"]
        touched = [m for m in pool if (m.get("status") or "new") != "new"]
        if touched and len(touched) < len(pool):
            pool = touched
    return sorted(pool, key=lambda m: m["pub_number"])[0]["pub_number"]


def group_same_source_duplicates(records_by_pub, edges):
    """Reconstructs same_source group membership from pairwise edges.

    `records_by_pub`: {pub_number: record} for the tenant's full record set
    (records need pub_number/source/url/pub_date/first_seen/deadline/status).
    `edges`: iterable of (pub_number_a, pub_number_b) — unresolved same_source
    pairs only (a caller should filter out resolved_by-set rows before
    calling; this function has no notion of "resolved").

    run.py writes a star topology (the kept record paired with each
    candidate, never candidate-to-candidate — see find_duplicate_groups'
    docstring), so a >2-member group is only recoverable by walking the
    edge graph, not by reading any single row. Returns {pub_number: group}
    for every pub_number that appears in >=1 edge; group is a dict shared by
    reference across every member of one connected component:
        {"size": int, "order_confident": bool,
         "canonical_pub_number": str,
         "members": [{"pub_number", "source", "url", "rank"}, ...]}
    `rank` is 1-based ascending-chronological position, or None when
    order_confident is False. `members` is sorted by rank (ranked members
    first), falling back to pub_number among unranked ones.
    """
    adjacency = defaultdict(set)
    for a, b in edges:
        adjacency[a].add(b)
        adjacency[b].add(a)

    result = {}
    visited = set()
    for start in adjacency:
        if start in visited:
            continue
        component, stack = set(), [start]
        while stack:
            node = stack.pop()
            if node in component:
                continue
            component.add(node)
            stack.extend(adjacency[node] - component)
        visited |= component

        members = [records_by_pub[p] for p in component if p in records_by_pub]
        if len(members) < 2:
            continue  # counterpart missing from this record set (e.g. another tenant's slice)

        ordering = _group_ordering(members)
        rank_by_pub = {m["pub_number"]: i for i, (_, m) in enumerate(ordering, start=1)} if ordering else {}
        group = {
            "size": len(members),
            "order_confident": ordering is not None,
            "canonical_pub_number": _pick_canonical(members),
            "members": sorted(
                [{"pub_number": m["pub_number"], "source": m["source"], "url": m.get("url", ""),
                  "rank": rank_by_pub.get(m["pub_number"])} for m in members],
                key=lambda x: (x["rank"] if x["rank"] is not None else 999, x["pub_number"])),
        }
        for p in component:
            if p in records_by_pub:
                result[p] = group
    return result
