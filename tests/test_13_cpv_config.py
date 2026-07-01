"""Step 13 — CPV config additions (CR-001 F7).
  Adds 35800000 (individual/support equipment), 39511100 (blankets),
  39522520 (camp beds), 39522540 (sleeping bags) to the active CPV set;
  confirms 44210000 stays out (construction/mechanical noise) and the
  explicit confirmed-keep 39522100 (awnings/tarpaulins) is untouched.
"""
import shutil
import config, match


F7_ADDED = {"35800000", "39511100", "39522520", "39522540"}


def test_f7_codes_present_in_active_set():
    assert F7_ADDED <= set(config.cpv_codes())


def test_f7_codes_resolve_in_cpv_reference():
    ref = config.cpv_reference()
    assert F7_ADDED <= set(ref)
    for code in F7_ADDED:
        for lang in ("en", "fr", "nl", "de"):
            assert ref[code][lang]  # non-empty official label in every language


def test_f7_codes_match_a_sample_notice():
    cpv_set = set(config.cpv_codes())
    rec_cpv_codes = ["39511100"]  # a blankets-only notice
    has_cpv = bool(set(rec_cpv_codes) & cpv_set)
    assert has_cpv
    assert match.classify_match(has_cpv, []) == "cpv"


def test_44210000_absent_and_does_not_match():
    cpv_set = set(config.cpv_codes())
    assert "44210000" not in cpv_set
    has_cpv = bool({"44210000"} & cpv_set)
    assert has_cpv is False


def test_confirmed_keep_39522100_still_active():
    assert "39522100" in config.cpv_codes()


def test_write_cpv_round_trip(tmp_path, monkeypatch):
    # Isolate write_cpv from the real config file — it writes to a hardcoded
    # ROOT/config path, so point ROOT at a scratch copy for this test only.
    scratch = tmp_path / "config"
    scratch.mkdir()
    shutil.copy(config.ROOT / "config" / "cpv.yaml", scratch / "cpv.yaml")
    shutil.copy(config.ROOT / "config" / "cpv_reference.json", scratch / "cpv_reference.json")
    monkeypatch.setattr(config, "ROOT", tmp_path)

    new_codes = sorted(F7_ADDED | {"39522530"})
    config.write_cpv(new_codes)
    assert config.cpv_codes() == new_codes
