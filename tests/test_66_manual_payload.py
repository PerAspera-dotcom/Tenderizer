"""The manual-entry form's request body against the API model.

Found 2026-11-13: ManualTenderBody refuses unknown fields (extra="forbid",
added in 74e8829), but the form built its body by spreading form state, so the
form-only `cpv_text` went along and every create, edit and confirm failed with
a bare 422. The handler tests called the handlers with hand-built bodies, so
they never saw what the form actually sends.

These tests read the keys `toInput` writes in ManualEntry.tsx and the
`ManualTenderInput` interface in types.ts, require both to equal the API
model's fields exactly, and push a body with exactly those keys through all
three endpoints.
"""
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

import api
import manual_tenders
import object_storage
import run
import store
from conftest import TEST_TENANT_ID, make_identity
from test_64_manual_drafts import FX_RATES, _ready_draft

FRONTEND = Path(__file__).resolve().parents[1] / "frontend" / "src"


def _to_input_keys():
    src = (FRONTEND / "pages" / "scout" / "ManualEntry.tsx").read_text(encoding="utf-8")
    m = re.search(r"function toInput\(f: Form\): ManualTenderInput \{\s*return \{(.*?)\n  \};", src, re.S)
    assert m, "toInput not found in ManualEntry.tsx"
    body = m.group(1)
    assert "..." not in body, "toInput must list its fields explicitly, not spread form state"
    return re.findall(r"^\s*(\w+):", body, re.M)


def _interface_keys():
    src = (FRONTEND / "types.ts").read_text(encoding="utf-8")
    m = re.search(r"export interface ManualTenderInput \{(.*?)\n\}", src, re.S)
    assert m, "ManualTenderInput not found in types.ts"
    return re.findall(r"^\s*(\w+)\??:", m.group(1), re.M)


API_FIELDS = list(api.ManualTenderBody.model_fields)


def test_to_input_sends_exactly_the_api_fields():
    keys = _to_input_keys()
    assert len(keys) == len(set(keys))
    assert sorted(keys) == sorted(API_FIELDS)


def test_the_typescript_input_type_matches_the_api_model():
    assert sorted(_interface_keys()) == sorted(API_FIELDS)


def test_the_api_model_covers_the_user_fields():
    assert sorted(API_FIELDS) == sorted(manual_tenders.USER_FIELDS)


def test_a_form_only_field_is_still_refused():
    # extra="forbid" stays: it is what caught the drift
    with pytest.raises(ValidationError):
        api.ManualTenderBody(**_payload(1), cpv_text="39522530")


# ── the body the form sends, through all three endpoints ─────────────────────

@pytest.fixture
def conn(tmp_path, monkeypatch):
    db_path = str(tmp_path / "t.db")
    monkeypatch.setattr(api, "DB_PATH", db_path)
    monkeypatch.setattr(object_storage, "LOCAL_ROOT", tmp_path / "objects")
    monkeypatch.delenv("DOCUMENTS_S3_BUCKET", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-not-a-real-key")
    monkeypatch.setattr(manual_tenders, "_fx_rates", lambda: FX_RATES)
    monkeypatch.setattr(run.translate, "translate_cached", lambda _c, t: ("EN: " + (t or ""), "ok"))
    c = store.init_db(db_path)
    store.ensure_tenant(c, TEST_TENANT_ID)
    return c


def _payload(site_id, **over):
    """What toInput produces for a filled-in form: every key it writes, as
    the JSON types it writes them (strings, a number id, a list of codes)."""
    values = {"origin_site_id": site_id, "url": "https://www.dtvp.de/notice/123",
              "tag_line": "Lieferung von Zelten", "description": "200 Zelte", "buyer": "BAAINBw",
              "country": "DEU", "place": "Koblenz (NUTS: DEA22)", "category": "Supply",
              "procedure": "open", "pub_date": "2026-07-10", "deadline": "2027-09-30",
              "cpv_codes": ["39522530", "35113400"], "value": "1200000", "value_currency": "EUR",
              "language": "deu"}
    values.update(over)
    return {k: values[k] for k in _to_input_keys()}


def test_create_accepts_the_form_body(conn):
    site = store.add_origin_site(conn, TEST_TENANT_ID, "DTVP")
    t = api.create_manual_tender(api.ManualTenderBody(**_payload(site["id"])), identity=make_identity())
    assert t["pub_number"].startswith("MAN-")


def test_edit_accepts_the_form_body(conn):
    site = store.add_origin_site(conn, TEST_TENANT_ID, "DTVP")
    t = api.create_manual_tender(api.ManualTenderBody(**_payload(site["id"])), identity=make_identity())
    out = api.update_manual_tender(t["pub_number"], api.ManualTenderBody(**_payload(site["id"], procedure="restricted")),
                                   identity=make_identity())
    assert out["tender"]["procedure"] == "restricted"


def test_confirm_accepts_the_form_body(conn):
    site = store.add_origin_site(conn, TEST_TENANT_ID, "DTVP")
    d = _ready_draft(conn)
    t = api.confirm_manual_draft(d["id"], api.ManualTenderBody(**_payload(site["id"])), identity=make_identity())
    assert t["pub_number"].startswith("MAN-")
