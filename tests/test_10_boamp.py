"""Step 10 — BOAMP connector + cross-source schema consistency.
Interface:
  connectors.boamp.build_params(keywords, cpv_codes, since) -> dict
  connectors.boamp.parse_response(json_data) -> list[dict]
  normalize.normalize_boamp(raw) -> normalised record (SAME schema as TED)
"""
from datetime import date
import normalize
from connectors import boamp

def test_normalize_boamp_country_is_fr(raw_boamp_supply):
    assert normalize.normalize_boamp(raw_boamp_supply)["country"] == "FR"

def test_normalize_boamp_fournitures_is_supply(raw_boamp_supply):
    assert normalize.normalize_boamp(raw_boamp_supply)["category"] == "Supply"

def test_normalize_boamp_maps_objet_to_tagline(raw_boamp_supply):
    r = normalize.normalize_boamp(raw_boamp_supply)
    assert r["tag_line"] == "Fourniture de tentes pour la protection civile"

def test_boamp_and_ted_share_identical_schema(raw_boamp_supply, raw_ted_supply):
    # the whole pipeline depends on every source producing the same keys
    assert set(normalize.normalize_boamp(raw_boamp_supply)) == \
           set(normalize.normalize_ted(raw_ted_supply))

def test_build_params_includes_date_and_keyword():
    p = boamp.build_params(["tente"], ["35521000"], date(2026,6,1))
    blob = str(p)
    assert "2026-06-01" in blob and "tente" in blob
