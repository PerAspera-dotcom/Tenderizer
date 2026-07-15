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


# ── A2: Expressions of Interest ──────────────────────────────────────────────

def test_eoi_phrase_english():
    assert classification.classify(_rec("Expression of interest: tent supply framework")) == "eoi"


def test_eoi_acronym():
    assert classification.classify(_rec("EOI - tent supply framework")) == "eoi"


def test_eoi_acronym_is_word_boundary_only():
    # "eoi" must not fire as a bare substring of an unrelated word.
    assert classification.classify(_rec("Geoinformatics services for mapping")) == "tender"


def test_eoi_phrase_french_accented():
    assert classification.classify(
        _rec("Appel à manifestation d'intérêt pour la fourniture de tentes")) == "eoi"


def test_past_tender_precedence_over_eoi():
    # A1 (empty deadline) is checked before A2 — an EOI-worded notice with no
    # deadline is still past_tender, per CR-002 A's precedence rule.
    assert classification.classify(
        _rec("Expression of interest: tent supply", deadline="")) == "past_tender"


# ── A4: Prequalification (D-A proposed defaults) ─────────────────────────────

def test_prequalification_english():
    assert classification.classify(_rec("Prequalification of tent suppliers")) == "prequalification"


def test_prequalification_pqq_acronym():
    assert classification.classify(_rec("PQQ - tent framework suppliers")) == "prequalification"


def test_prequalification_french_selection_des_candidats():
    assert classification.classify(
        _rec("Sélection des candidats pour la fourniture de tentes")) == "prequalification"


def test_restricted_procedure_alone_is_not_prequalification():
    # "procedure restreinte" is a common, generic French procedure label —
    # must NOT trigger prequalification on its own (false-positive guard).
    assert classification.classify(
        _rec("Fourniture de tentes - procédure restreinte")) == "tender"


def test_restricted_procedure_with_call_for_candidates_is_prequalification():
    assert classification.classify(
        _rec("Fourniture de tentes - procédure restreinte, appel à candidatures")) == "prequalification"


def test_prequalification_precedence_over_eoi():
    # Both A4 and A2 terms present -> prequalification wins (checked first).
    assert classification.classify(
        _rec("PQQ - Expression of interest for tent suppliers")) == "prequalification"


# ── A3: Prior Information Notice (D-B decided, stored as notice_type=fbo) ───

def test_pin_english():
    assert classification.classify(_rec("Prior information notice: future tent framework")) == "fbo"


def test_pin_french_accented():
    assert classification.classify(_rec("Avis de préinformation - fourniture de tentes")) == "fbo"


def test_bare_pin_acronym_does_not_match():
    # Deliberately not a standalone term (collision risk) — see D-B comment.
    assert classification.classify(_rec("Please enter your PIN to access the portal")) == "tender"


def test_fbo_is_last_in_precedence():
    # eoi wins over fbo/pin when both terms are present.
    assert classification.classify(
        _rec("Prior information notice - Expression of interest for tent suppliers")) == "eoi"


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
