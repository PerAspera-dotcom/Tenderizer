"""CR-004 F4 — ops CLI: `tenderizer run-now|backup|restore`.

A thin argparse wrapper over run.py/backup.py so an operator doesn't need a
Python shell for routine tasks. Installed as the `tenderizer` console script
(see pyproject.toml); also runnable directly as `python src/cli.py ...`.
"""
import argparse
import sys
from datetime import date, timedelta


def _run_now(args):
    import store
    import run as engine
    from schema import DEFAULT_TENANT_ID
    since = date.today() - timedelta(days=30)
    conn = store.init_db("data/tenders.db")
    store.ensure_tenant(conn, DEFAULT_TENANT_ID)
    sources = engine._default_sources(conn, DEFAULT_TENANT_ID, since)
    health = engine.run_pipeline(sources, "data/tenders.db", "reports/tenders.xlsx")
    for name, status in health.items():
        print(f"  {name}: {status}")


def _backup(args):
    import backup
    result = backup.run_backup()
    print(result)
    if result["status"] != "ok":
        sys.exit(1)


def _restore(args):
    import backup
    if not args.yes:
        print(f"This will overwrite the live database with the backup for {args.date}.")
        print("Re-run with --yes to confirm.")
        sys.exit(1)
    result = backup.restore_backup(args.date, confirm=True)
    print(result)


def main():
    parser = argparse.ArgumentParser(prog="tenderizer")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("run-now", help="Run the scrape/match/filter pipeline immediately.").set_defaults(func=_run_now)

    sub.add_parser("backup", help="Run a backup immediately.").set_defaults(func=_backup)

    restore_p = sub.add_parser("restore", help="Restore the database from a backup.")
    restore_p.add_argument("date", help="Backup date to restore, e.g. 2026-07-01 or 20260701.")
    restore_p.add_argument("--yes", action="store_true", help="Confirm this destructive operation.")
    restore_p.set_defaults(func=_restore)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
