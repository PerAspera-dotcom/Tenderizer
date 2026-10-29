"""Per-source attribution and disclaimer text, for sources whose reuse
permission comes with conditions (2026-10-29: BOSA's e-Procurement go-live
is conditional on attribution and a disclaimer). Shown on the tender detail
panes (frontend/src/utils.ts SOURCE_NOTICES — keep the wording identical)
and appended to outgoing tender emails (digest, forward).

No BOSA logo anywhere: its terms of use (Intellectual property rights)
permit logo use only with BOSA's prior permission.
"""

SOURCE_NOTICES = {
    "e-Procurement": {
        "attribution": "Source: Belgian e-Procurement platform (publicprocurement.be), FPS BOSA.",
        "disclaimer": ("Tenderizer is not affiliated with or endorsed by FPS BOSA. Notice data is "
                       "reproduced as published; FPS BOSA gives no guarantee as to its accuracy or "
                       "currency. Always check the original publication before acting on it."),
    },
}


def email_footer(sources):
    """Footer text for an email listing tenders from `sources`, or "" when
    none of them carries a notice."""
    blocks = [f"{n['attribution']} {n['disclaimer']}"
              for s in sorted(set(sources)) if (n := SOURCE_NOTICES.get(s))]
    return "\n\n".join(["--"] + blocks) if blocks else ""
