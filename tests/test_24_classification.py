"""Step 24 — CR-002 §A: notice-type classification.

classify(rec) -> notice_type:str, always populated (default "tender", never
blank). extract_award_info(rec) -> (awarded_to, awarded_value,
awarded_currency), each None when not found in the notice text — never
fabricated.
"""
import classification


def _rec(tag_line="", description="", deadline="2030-01-01T00:00:00+00:00"):
    return {"tag_line": tag_line, "description": description, "deadline": deadline}


def test_default_type_is_tender():
    assert classification.classify(_rec("Supply of tents")) == "tender"


def test_empty_deadline_is_past_tender():
    assert classification.classify(_rec("Supply of tents", deadline="")) == "past_tender"


def test_none_deadline_is_past_tender():
    rec = {"tag_line": "Supply of tents", "description": "", "deadline": None}
    assert classification.classify(rec) == "past_tender"


def test_whitespace_only_deadline_is_past_tender():
    assert classification.classify(_rec("Supply of tents", deadline="   ")) == "past_tender"


def test_future_deadline_stays_tender():
    assert classification.classify(_rec("Supply of tents", deadline="2030-06-01T00:00:00+00:00")) == "tender"


# ── Award info extraction ────────────────────────────────────────────────────

def test_extract_awarded_to_english():
    rec = _rec("Tents — award notice",
                description="Contract awarded to Acme Shelters Ltd. Delivery within 60 days.")
    awarded_to, value, currency = classification.extract_award_info(rec)
    assert awarded_to == "Acme Shelters Ltd"


def test_extract_awarded_to_french_attributaire():
    rec = _rec("Tentes — avis d'attribution",
                description="Attributaire : Société Tentes de France. Notification le 12 juin.")
    awarded_to, value, currency = classification.extract_award_info(rec)
    assert awarded_to == "Société Tentes de France"


def test_extract_value_english():
    rec = _rec("Tents — award notice",
                description="The contract value of 350000 EUR was accepted by the buyer.")
    awarded_to, value, currency = classification.extract_award_info(rec)
    assert value == "350000"
    assert currency == "EUR"


def test_extract_value_french_montant():
    rec = _rec("Tentes — avis d'attribution",
                description="Montant du marché : 275000 EUR net de taxes.")
    awarded_to, value, currency = classification.extract_award_info(rec)
    assert value == "275000"
    assert currency == "EUR"


def test_extract_returns_none_when_absent():
    rec = _rec("Supply of tents", description="Standard call for tenders, no award info here.")
    awarded_to, value, currency = classification.extract_award_info(rec)
    assert awarded_to is None
    assert value is None
    assert currency is None
