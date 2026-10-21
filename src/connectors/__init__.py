class Records(list):
    """A connector's fetch() result: a plain list plus an optional `warning`
    that run.py records on the source's health row (e.g. skipped days, a
    pagination cut short), so a partial fetch is visible on the dashboard
    instead of reading as a clean "ok"."""
    warning = None
