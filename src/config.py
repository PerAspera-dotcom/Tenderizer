"""Loads the YAML config files. All relevance logic lives in config/, not in code."""
import json
import pathlib
import re
import yaml
from unidecode import unidecode

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _load(name):
    with open(ROOT / "config" / name, encoding="utf-8") as f:
        return yaml.safe_load(f)


def cpv_reference():
    """Official CPV metadata (code -> {en, fr, nl, de, group, category}).

    Use for validating codes and for showing human labels in the report.
    """
    with open(ROOT / "config" / "cpv_reference.json", encoding="utf-8") as f:
        return json.load(f)["codes"]


def cpv_label(code, lang="en"):
    """Human label for a CPV code in the given language; falls back to the code itself."""
    entry = cpv_reference().get(code)
    return entry.get(lang) if entry else code


def cpv_codes():
    """List of CPV code strings."""
    return _load("cpv.yaml")["codes"]


def keywords():
    """Flat list of all keyword terms across every language (the full reference library).

    Used client-side to tag which terms matched a result — NOT in the live TED query.
    """
    terms = _load("keywords.yaml")["terms"]
    return [w for lang in terms.values() for w in lang]


def distinctive_keywords():
    """High-signal subset fed into the live TED title query (notice-title = exact match).

    Broad words are excluded here because they flood the title search; see keywords.yaml.
    """
    return _load("keywords.yaml")["distinctive"]


def portals():
    """List of portal config dicts (name, type, enabled)."""
    return _load("portals.yaml")


def exclusions():
    """Active exclusion rules (name -> {codes: [...], terms: {lang: [...]}}).

    See config/exclusions.yaml. Applied by filters.py as a post-match stage.
    """
    return _load("exclusions.yaml")


def term_code_gaps():
    """CR-001 F8 consistency check: report/review-only, not enforced.

    codes_without_terms: active CPV codes whose official label (any language) shares
        no word with any keyword term — a candidate for adding safeguard terms.
    terms_without_codes: keyword terms that share no word with any active code's
        official label — expected for broad supplementary safeguard vocabulary
        (e.g. 'gazebo', 'bivouac') that was never meant to trace to one code 1:1.
    """
    def _words(text):
        return set(re.findall(r"\w+", unidecode(text or "").lower()))

    ref = cpv_reference()
    active = cpv_codes()
    term_words = set()
    for term in keywords():
        term_words |= _words(term)

    label_words = set()
    codes_without_terms = []
    for code in active:
        entry = ref.get(code, {})
        this_code_words = set()
        for lang in ("en", "fr", "nl", "de"):
            this_code_words |= _words(entry.get(lang, ""))
        label_words |= this_code_words
        if not (this_code_words & term_words):
            codes_without_terms.append(code)

    terms_without_codes = [t for t in keywords() if not (_words(t) & label_words)]
    return {"codes_without_terms": codes_without_terms, "terms_without_codes": terms_without_codes}


def write_cpv(codes):
    """Validate CPV codes against cpv_reference.json then overwrite cpv.yaml."""
    import warnings
    ref = cpv_reference()
    unknown = [c for c in codes if c not in ref]
    if unknown:
        warnings.warn(f"Unknown CPV codes (not in cpv_reference.json): {unknown}")
    data = _load("cpv.yaml")
    data["codes"] = codes
    with open(ROOT / "config" / "cpv.yaml", "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)


def write_keywords(data):
    """Merge data into keywords.yaml (keys: terms and/or distinctive)."""
    current = _load("keywords.yaml")
    if "terms" in data:
        current["terms"] = data["terms"]
    if "distinctive" in data:
        current["distinctive"] = data["distinctive"]
    with open(ROOT / "config" / "keywords.yaml", "w", encoding="utf-8") as f:
        yaml.dump(current, f, default_flow_style=False, allow_unicode=True)
