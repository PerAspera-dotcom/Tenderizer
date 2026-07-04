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
