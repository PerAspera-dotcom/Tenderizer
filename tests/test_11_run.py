"""Step 11 — end-to-end run / scheduler entrypoint.
Interface:
  run.run_pipeline(sources, db_path, out_path) -> health(dict)
  where each source = {"name":str, "fetch":callable->list[raw], "normalize":callable}
  A failing source must NOT abort the run; it is recorded in health.
"""
from openpyxl import load_workbook
import run

def _src_ok(raw_ted_supply):
    return {"name":"TED","fetch":lambda:[raw_ted_supply],
            "normalize":__import__("normalize").normalize_ted}

def _src_boom():
    def boom(): raise RuntimeError("portal down")
    return {"name":"BROKEN","fetch":boom,"normalize":lambda r:r}

def test_end_to_end_produces_report(tmp_path, raw_ted_supply):
    health = run.run_pipeline([_src_ok(raw_ted_supply)],
                              str(tmp_path/"t.db"), str(tmp_path/"r.xlsx"))
    assert "ok" in health["TED"]
    assert load_workbook(str(tmp_path/"r.xlsx"))

def test_failing_source_is_isolated(tmp_path, raw_ted_supply):
    health = run.run_pipeline([_src_ok(raw_ted_supply), _src_boom()],
                              str(tmp_path/"t.db"), str(tmp_path/"r.xlsx"))
    assert "ok" in health["TED"]            # good source still ran
    assert "error" in health["BROKEN"]      # bad source captured, not fatal

def test_run_is_idempotent(tmp_path, raw_ted_supply):
    import store
    db = str(tmp_path/"t.db"); out = str(tmp_path/"r.xlsx")
    run.run_pipeline([_src_ok(raw_ted_supply)], db, out)
    run.run_pipeline([_src_ok(raw_ted_supply)], db, out)   # second run, same data
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
    run.run_pipeline([src], db, out)

    conn = store.init_db(db)
    stored = store.all_records(conn)
    assert stored[0]["exclude_reason"] == "container_modular_prefab"  # auditable, not deleted

    wb = load_workbook(out)
    pub_numbers = [c.value for ws in wb.worksheets for row in ws.iter_rows() for c in row]
    assert "999999-2026" not in pub_numbers                            # but not surfaced
