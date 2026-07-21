"""One-off backfill: populate awarded_to/awarded_value/awarded_currency (and,
as of the past-tenders data-coverage follow-up, the richer award_detail —
winner registration number/city/NUTS/size, lot/contract identifiers,
framework max value) for already-stored `past_tender` records, now that
normalize_ted/normalize_boamp fetch the structured award fields CR-002 A1
needed but never wired up (CR-003 G4 — see connectors/ted.py's FIELDS
comment and normalize.py's _boamp_award_info for what changed and why).

update_classification() is the same escape hatch CR-002's own
scratch_backfill_notice_type.py uses: upsert() is insert-only, so an
already-stored row never picks up a normalize.py fix on its own. Re-fetches
each stored past_tender notice by pub_number/idweb (raw award payloads were
never cached) and fills in only currently-null award fields — never
overwrites a value already populated. Re-running after the award_detail
fields were added re-processes rows that already have awarded_to/value/
currency but are still missing award_detail — everything else about them is
skipped (already filled, never overwritten).

Run from the project root:  python scratch_backfill_award_info.py
"""
import sys
from collections import Counter

sys.path.insert(0, "src")

import classification
import store
from connectors import boamp, ted
from normalize import normalize_boamp, normalize_ted
from schema import tenders

DB_PATH = "data/tenders.db"
BATCH = 50


def _refetch_ted_by_pub_number(pub_numbers):
    raw_by_pub = {}
    pub_numbers = sorted(pub_numbers)
    for i in range(0, len(pub_numbers), BATCH):
        chunk = pub_numbers[i:i + BATCH]
        query = "publication-number IN (" + ", ".join(f'"{p}"' for p in chunk) + ")"
        import requests
        body = {"query": query, "fields": ted.FIELDS, "limit": BATCH,
                 "scope": "ACTIVE", "paginationMode": "ITERATION", "checkQuerySyntax": False}
        resp = requests.post(ted.ENDPOINT, json=body, timeout=60)
        resp.raise_for_status()
        for raw in ted.parse_response(resp.json()):
            raw_by_pub[raw.get("publication-number")] = raw
    return raw_by_pub


def _refetch_boamp_by_idweb(idwebs):
    """Same batched idweb-lookup pattern as scratch_backfill_boamp_cpv.py."""
    raw_by_idweb = {}
    idwebs = sorted(idwebs)
    for i in range(0, len(idwebs), BATCH):
        chunk = idwebs[i:i + BATCH]
        where = "idweb in (" + ", ".join(f'"{w}"' for w in chunk) + ")"
        import requests
        resp = requests.get(boamp.ENDPOINT, params={"where": where, "limit": BATCH}, timeout=60)
        resp.raise_for_status()
        for raw in boamp.parse_response(resp.json()):
            raw_by_idweb[raw.get("idweb")] = raw
    return raw_by_idweb


def backfill_tenant(conn, tenant_id):
    records = store.all_records(conn, tenant_id)
    pending = [r for r in records if r.get("notice_type") == "past_tender"
               and not (r.get("awarded_to") and r.get("awarded_value")
                        and r.get("awarded_currency") and r.get("award_detail"))]
    if not pending:
        return None

    ted_records = [r for r in pending if r.get("source") == "TED"]
    boamp_records = [r for r in pending if r.get("source") == "BOAMP"]
    raw_by_pub = {}
    raw_by_pub.update(_refetch_ted_by_pub_number([r["pub_number"] for r in ted_records]))
    raw_by_idweb = _refetch_boamp_by_idweb([r["pub_number"] for r in boamp_records])

    stats = Counter()
    for rec in pending:
        if rec.get("source") == "TED":
            raw = raw_by_pub.get(rec["pub_number"])
            normalized = normalize_ted(raw) if raw is not None else None
        else:
            raw = raw_by_idweb.get(rec["pub_number"])
            normalized = normalize_boamp(raw) if raw is not None else None

        if normalized is None:
            stats["not_found_on_live_source"] += 1  # e.g. archived/no longer indexed
            continue

        merged = dict(rec, raw_award_winner=normalized["raw_award_winner"],
                      raw_award_value=normalized["raw_award_value"],
                      raw_award_currency=normalized["raw_award_currency"],
                      raw_award_detail=normalized["raw_award_detail"])
        awarded_to, awarded_value, awarded_currency, award_detail = classification.extract_award_info(merged)

        # Never overwrite a field the record already had — only fill nulls.
        final_to = rec.get("awarded_to") or awarded_to
        final_value = rec.get("awarded_value") or awarded_value
        final_currency = rec.get("awarded_currency") or awarded_currency
        final_detail = rec.get("award_detail") or award_detail

        if (final_to, final_value, final_currency, final_detail) != (
                rec.get("awarded_to"), rec.get("awarded_value"),
                rec.get("awarded_currency"), rec.get("award_detail")):
            store.update_classification(conn, tenant_id, rec["pub_number"],
                                         notice_type=rec["notice_type"], awarded_to=final_to,
                                         awarded_value=final_value, awarded_currency=final_currency,
                                         award_detail=final_detail)
            stats["updated"] += 1
            if final_detail and not rec.get("award_detail"):
                stats["gained_award_detail"] += 1
        else:
            stats["unchanged"] += 1
    stats["total_pending"] = len(pending)
    return stats


def main():
    conn = store.init_db(DB_PATH)
    with conn.connect() as c:
        from sqlalchemy import select
        tenant_ids = [row[0] for row in c.execute(select(tenders.c.tenant_id).distinct())]

    for tenant_id in tenant_ids:
        stats = backfill_tenant(conn, tenant_id)
        if stats is None:
            continue
        print(f"tenant {tenant_id}: {dict(stats)}")


if __name__ == "__main__":
    main()
