"""Step 11 — end-to-end run / scheduler entrypoint.
Interface:
  run.run_pipeline(sources, db_path, out_path, now=None, fx_rates=None) -> health(dict)
  where each source = {"name":str, "fetch":callable->list[raw], "normalize":callable}
  A failing source must NOT abort the run; it is recorded in health.
  `fx_rates` defaults to a live ECB fetch (CR-001 D2) — tests pass a fixed
  snapshot so the suite doesn't depend on network access.
"""
from openpyxl import load_workbook
import run

FX_RATES = {"date": "2026-07-01", "rates": {"EUR": 1.0, "SEK": 11.23}}

def _src_ok(raw_ted_supply):
    return {"name":"TED","fetch":lambda:[raw_ted_supply],
            "normalize":__import__("normalize").normalize_ted}

def _src_boom():
    def boom(): raise RuntimeError("portal down")
    return {"name":"BROKEN","fetch":boom,"normalize":lambda r:r}

def test_end_to_end_produces_report(tmp_path, raw_ted_supply):
    health = run.run_pipeline([_src_ok(raw_ted_supply)],
                              str(tmp_path/"t.db"), str(tmp_path/"r.xlsx"), fx_rates=FX_RATES)
    assert "ok" in health["TED"]
    assert load_workbook(str(tmp_path/"r.xlsx"))

def test_failing_source_is_isolated(tmp_path, raw_ted_supply):
    health = run.run_pipeline([_src_ok(raw_ted_supply), _src_boom()],
                              str(tmp_path/"t.db"), str(tmp_path/"r.xlsx"), fx_rates=FX_RATES)
    assert "ok" in health["TED"]            # good source still ran
    assert "error" in health["BROKEN"]      # bad source captured, not fatal

def test_run_is_idempotent(tmp_path, raw_ted_supply):
    import store
    db = str(tmp_path/"t.db"); out = str(tmp_path/"r.xlsx")
    run.run_pipeline([_src_ok(raw_ted_supply)], db, out, fx_rates=FX_RATES)
    run.run_pipeline([_src_ok(raw_ted_supply)], db, out, fx_rates=FX_RATES)   # second run, same data
    conn = store.init_db(db)
    assert len(store.all_records(conn)) == 1               # no duplicate

def test_excluded_notice_is_stored_but_not_reported(tmp_path, raw_ted_supply):
    # CR-001 F3: container/modular/prefab notices are auditable (kept in the DB with
    # exclude_reason set) but must not surface in the report.
    import store, copy
    raw = copy.deepcopy(raw_ted_supply)
    raw["publication-number"] = "999999-2026"
    raw["notice-title"] = {"eng": "Sweden – Supply of modular prefabricated cabins"}
    raw["classification-cpv"] = ["44211100"]
    db = str(tmp_path/"t.db"); out = str(tmp_path/"r.xlsx")
    src = {"name": "TED", "fetch": lambda: [raw], "normalize": __import__("normalize").normalize_ted}
    run.run_pipeline([src], db, out, fx_rates=FX_RATES)

    conn = store.init_db(db)
    stored = store.all_records(conn)
    assert stored[0]["exclude_reason"] == "container_modular_prefab"  # auditable, not deleted

    wb = load_workbook(out)
    pub_numbers = [c.value for ws in wb.worksheets for row in ws.iter_rows() for c in row]
    assert "999999-2026" not in pub_numbers                            # but not surfaced

def test_below_value_floor_is_excluded_after_fx_conversion(tmp_path, raw_ted_supply):
    # CR-001 F6: 100,000 SEK / 11.23 =~ 8,904 EUR — well under the 200k floor.
    import store, copy
    raw = copy.deepcopy(raw_ted_supply)
    raw["publication-number"] = "888888-2026"
    raw["estimated-value-proc"] = "100000"
    raw["estimated-value-cur-proc"] = "SEK"
    db = str(tmp_path/"t.db"); out = str(tmp_path/"r.xlsx")
    src = {"name": "TED", "fetch": lambda: [raw], "normalize": __import__("normalize").normalize_ted}
    run.run_pipeline([src], db, out, fx_rates=FX_RATES)

    conn = store.init_db(db)
    stored = store.all_records(conn)[0]
    assert stored["exclude_reason"] == "below_value_floor"
    assert stored["fx_rate_date"] == "2026-07-01"   # conversion is reproducible (D2)

def test_above_value_floor_is_kept(tmp_path, raw_ted_supply):
    import store, copy
    raw = copy.deepcopy(raw_ted_supply)
    raw["publication-number"] = "777777-2026"
    raw["estimated-value-proc"] = "5000000"
    raw["estimated-value-cur-proc"] = "SEK"
    db = str(tmp_path/"t.db"); out = str(tmp_path/"r.xlsx")
    src = {"name": "TED", "fetch": lambda: [raw], "normalize": __import__("normalize").normalize_ted}
    run.run_pipeline([src], db, out, fx_rates=FX_RATES)

    conn = store.init_db(db)
    assert store.all_records(conn)[0]["exclude_reason"] == ""
