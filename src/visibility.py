"""CR-013 §6 — the one rule for whether an excluded tender is hidden.

Every place that used to drop a record on `exclude_reason` alone calls this
instead: the Review Queue list and detail (api.py), same-source and
cross-portal dedup candidates (dedup.py), and the run's translation,
screening and report passes (run.py).

For a scraped tender nothing changes: excluded means hidden, exactly as
before. A manual tender is never hidden by an automated rule — a person
deliberately added it (the client's standing "rather too many tenders than
too few", CR-008 P0). Its exclude_reason is still computed and stored, and
the UI shows it as "would be excluded: <reason>".

Soft-deleted rows don't reach any of these callers: store.all_records leaves
them out.
"""


def is_manual(record):
    return record.get("entry_method") == "manual"


def is_hidden(record):
    return bool(record.get("exclude_reason")) and not is_manual(record)
