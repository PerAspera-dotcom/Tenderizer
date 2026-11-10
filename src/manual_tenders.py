"""CR-013 §4 — manual tender entry: create, edit, soft-delete, restore.

A manual tender is a normal `tenders` row with source='Manual',
entry_method='manual' and a server-generated, immutable pub_number
(MAN-{year}-{NNNN}). Its hash is sha256("Manual|<pub_number>"), so nothing the
user types feeds it and every user field stays editable.

Everything the system derives — matching tags, EUR value + FX date, the
exclusion, classification, award info, translation — goes through the same
functions a scraped record does (run.derive_fields, run.translate_record).
The exclusion is computed and stored but never hides the record
(visibility.is_hidden).

`created_by`/`created_at` are stamped here from the authenticated identity;
the API model refuses any client-supplied identity, status or derived field.
"""
import re
from datetime import date, datetime, timezone

import currency
import run
import store

# The fields a person supplies (CR-013 §4). Everything else is derived.
USER_FIELDS = ("origin_site_id", "url", "tag_line", "description", "buyer", "country", "place",
               "category", "procedure", "pub_date", "deadline", "cpv_codes", "value",
               "value_currency", "language")
# Recorded in tender_history on edit: what the person changed, plus the
# derived exclusion (so "why is this flagged now" has an answer).
HISTORY_FIELDS = USER_FIELDS + ("exclude_reason",)

_CPV = re.compile(r"^(\d{8})(?:-\d)?$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")
_CURRENCY = re.compile(r"^[A-Z]{3}$")
_LANGUAGE = re.compile(r"^[a-z]{3}$")


class ManualTenderError(ValueError):
    """Bad input. `field` names the offending field for the form."""

    def __init__(self, field, message):
        super().__init__(message)
        self.field = field


class NotEditable(LookupError):
    """No live manual tender with that pub_number for this tenant."""


# ── input cleaning ───────────────────────────────────────────────────────────

def _text(v):
    return " ".join(str(v).split()) if v is not None else ""


def _clean_date(field, v):
    v = (v or "").strip()
    if not v:
        return ""
    if not _DATE.match(v):
        raise ManualTenderError(field, "Use a date like 2026-12-31")
    try:
        date.fromisoformat(v[:10])
    except ValueError:
        raise ManualTenderError(field, "Not a real date")
    return v


def _clean_cpv(codes):
    out = []
    for raw in codes or []:
        m = _CPV.match(str(raw).strip())
        if not m:
            raise ManualTenderError("cpv_codes", f"'{raw}' is not a CPV code (8 digits, e.g. 39522530)")
        if m.group(1) not in out:
            out.append(m.group(1))
    return out


def _clean_value(value, cur):
    value = (str(value).strip().replace(",", "") if value not in (None, "") else "")
    cur = (cur or "").strip().upper()
    if value:
        try:
            if float(value) < 0:
                raise ValueError
        except ValueError:
            raise ManualTenderError("value", "Enter a number, e.g. 250000")
        if not _CURRENCY.match(cur):
            raise ManualTenderError("value_currency", "Pick the currency of the value")
    elif cur and not _CURRENCY.match(cur):
        raise ManualTenderError("value_currency", "Use a 3-letter currency code, e.g. EUR")
    return value, (cur if value else "")


def clean(conn, tenant_id, fields):
    """Validate and normalise the user-supplied fields. Raises
    ManualTenderError naming the field."""
    f = {k: fields.get(k) for k in USER_FIELDS}
    site_id = f["origin_site_id"]
    if site_id in (None, ""):
        raise ManualTenderError("origin_site_id", "Choose where this tender was found")
    if store.get_origin_site(conn, tenant_id, int(site_id)) is None:
        raise ManualTenderError("origin_site_id", "Unknown site")
    tag_line = _text(f["tag_line"])
    if not tag_line:
        raise ManualTenderError("tag_line", "A title is required")
    language = (f["language"] or "eng").strip().lower()
    if not _LANGUAGE.match(language):
        raise ManualTenderError("language", "Use a 3-letter language code, e.g. eng, deu, fra")
    value, cur = _clean_value(f["value"], f["value_currency"])
    pub_date = _clean_date("pub_date", f["pub_date"])
    deadline = _clean_date("deadline", f["deadline"])
    if pub_date and deadline and deadline[:10] < pub_date[:10]:
        raise ManualTenderError("deadline", "The deadline is before the publication date")
    return {
        "origin_site_id": int(site_id),
        "url": (f["url"] or "").strip(),
        "tag_line": tag_line,
        "description": (f["description"] or "").strip(),
        "buyer": _text(f["buyer"]),
        "country": _text(f["country"]),
        "place": _text(f["place"]),
        "category": _text(f["category"]),
        "procedure": _text(f["procedure"]),
        "pub_date": pub_date,
        "deadline": deadline,
        "cpv_codes": _clean_cpv(f["cpv_codes"]),
        "value": value,
        "value_currency": cur,
        "language": language,
    }


# ── derivation (shared with scraped records) ─────────────────────────────────

_fx_cache = {}


def _fx_rates():
    """The day's ECB snapshot, fetched once per process per day (a scheduled
    run fetches once per run; a form submission shouldn't fetch every time)."""
    today = date.today().isoformat()
    if today not in _fx_cache:
        _fx_cache.clear()
        _fx_cache[today] = currency.fetch_ecb_rates_or_fallback()
    return _fx_cache[today]


def _derive(conn, tenant_id, rec, now=None, fx_rates=None):
    full_keywords, cpv_set, exclusions = run.matching_context(conn, tenant_id)
    return run.derive_fields(rec, full_keywords, cpv_set, exclusions,
                             now or datetime.now(timezone.utc), fx_rates or _fx_rates())


# ── operations ───────────────────────────────────────────────────────────────

def create(conn, tenant_id, created_by, fields, now=None, fx_rates=None):
    """Create a manual tender; returns the stored record."""
    rec = clean(conn, tenant_id, fields)
    now = now or datetime.now(timezone.utc)
    rec.update({
        "source": store.MANUAL_SOURCE,
        "entry_method": "manual",
        "status": "new",
        "first_seen": now.date().isoformat(),
        "created_by": created_by,
        "created_at": now.isoformat(timespec="seconds"),
        "internal_identifier": None,
        "supersedes": [],
        "tag_line_en": "", "description_en": "", "translation_status": "",
    })
    _derive(conn, tenant_id, rec, now, fx_rates)  # independent of pub_number
    with conn.begin() as c:
        rec["pub_number"] = store.next_manual_pub_number(c, tenant_id, now.year)
        if not store.insert_tender(c, tenant_id, rec):
            raise RuntimeError(f"pub_number collision on {rec['pub_number']}")  # cannot happen
    run.translate_record(conn, tenant_id, store.get_record(conn, tenant_id, rec["pub_number"]))
    return store.get_record(conn, tenant_id, rec["pub_number"])


def update(conn, tenant_id, pub_number, edited_by, fields, now=None, fx_rates=None):
    """Replace the user-supplied fields of a live manual tender and re-derive
    everything else. hash/source/pub_number/created_* never change. Returns
    (record, changed_fields)."""
    old = store.get_record(conn, tenant_id, pub_number)
    if old is None or old.get("entry_method") != "manual":
        raise NotEditable(pub_number)
    rec = dict(old)
    rec.update(clean(conn, tenant_id, fields))
    _derive(conn, tenant_id, rec, now, fx_rates)
    text_changed = any(old.get(k) != rec.get(k) for k in ("tag_line", "description", "language"))
    if text_changed:  # the old translation no longer describes this text
        rec.update({"tag_line_en": "", "description_en": "", "translation_status": ""})
    try:
        changed = store.update_manual_tender(conn, tenant_id, pub_number, rec, edited_by, HISTORY_FIELDS)
    except LookupError:
        raise NotEditable(pub_number)
    if text_changed:
        run.translate_record(conn, tenant_id, store.get_record(conn, tenant_id, pub_number))
    return store.get_record(conn, tenant_id, pub_number), changed


def delete(conn, tenant_id, pub_number, deleted_by):
    if not store.set_manual_tender_deleted(conn, tenant_id, pub_number, True, deleted_by):
        raise NotEditable(pub_number)


def restore(conn, tenant_id, pub_number, restored_by):
    if not store.set_manual_tender_deleted(conn, tenant_id, pub_number, False, restored_by):
        raise NotEditable(pub_number)
    return store.get_record(conn, tenant_id, pub_number)
