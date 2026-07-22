"""Notifications & workflow — daily per-tenant digest email: new matches
today + pipeline items closing soon. Deliberately its own module (mirrors
alerts.py/backup.py rather than growing api.py/store.py) since building the
digest body is pure logic, no side effects — src/api.py's _run_daily_digest
is the only caller and owns sending it.

Urgency thresholds (≤7 days = urgent, 8-14 = warning) mirror
frontend/src/utils.ts's daysLeft() usage in PortalPipeline.tsx/
PortalCalendar.tsx exactly, so the email and the on-screen UI never disagree
about what counts as closing soon.
"""
from datetime import date, datetime, timezone

import store

URGENT_DAYS = 7
WARNING_DAYS = 14


def _days_left(iso_date):
    if not iso_date:
        return None
    try:
        d = datetime.fromisoformat(iso_date.replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return (d.date() - datetime.now(timezone.utc).date()).days


def build_daily_digest(conn, tenant_id, today=None):
    """Plain-text digest body, or None if there's nothing to report (no
    email gets sent on a quiet day).
    """
    today = today or date.today().isoformat()

    new_tenders = [r for r in store.all_records(conn, tenant_id)
                   if r["first_seen"] == today and r["status"] == "new"]

    closing_soon = []
    for e in store.get_pipeline_entries(conn, tenant_id):
        if e["submission_status"] == "submitted":
            continue
        days = _days_left(e["deadline_override"] or e["deadline"])
        if days is not None and days <= WARNING_DAYS:
            closing_soon.append((days, e))
    closing_soon.sort(key=lambda pair: pair[0])

    if not new_tenders and not closing_soon:
        return None

    sections = []
    if new_tenders:
        lines = [f"- {r['tag_line']} ({r['pub_number']}) — {r['buyer']}" for r in new_tenders]
        sections.append(f"New matches today ({len(new_tenders)}):\n" + "\n".join(lines))
    if closing_soon:
        lines = []
        for days, e in closing_soon:
            flag = "\U0001F534" if days <= URGENT_DAYS else "\U0001F7E1"
            lines.append(f"- {flag} {e['tag_line']} ({e['pub_number']}) — {days} day{'s' if days != 1 else ''} left")
        sections.append(f"Closing soon ({len(closing_soon)}):\n" + "\n".join(lines))

    return "\n\n".join(sections)
