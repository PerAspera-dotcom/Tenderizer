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


def _storage_check(args):
    import storage_migration
    if not storage_migration.check():
        sys.exit(1)


def _storage_migrate(args):
    import store
    import storage_migration
    engine = store.init_db("data/tenders.db")
    stats, missing = storage_migration.migrate(engine, dry_run=not args.yes)
    print("DRY RUN — nothing written" if not args.yes else "Migration pass complete")
    for k, v in sorted(stats.items()):
        print(f"  {k}: {v}")
    print(f"  rows whose file is missing: {len(missing)}")
    if not args.yes:
        print("Re-run with --yes to copy.")


def _storage_restore_local(args):
    import storage_migration
    stats = storage_migration.restore_local(dry_run=not args.yes)
    print("DRY RUN — nothing written" if not args.yes else "Restore pass complete")
    for k, v in sorted(stats.items()):
        print(f"  {k}: {v}")


def _screen_backfill(args):
    """CR-015 §3.4 — screening backfill, bounded and explicitly invoked."""
    import store
    import screening
    conn = store.init_db("data/tenders.db")
    statuses = tuple(s.strip() for s in args.status.split(",") if s.strip())
    summary = screening.backfill(conn, args.tenant, args.limit, statuses=statuses, dry_run=args.dry_run)
    print("DRY RUN — no Claude calls made" if args.dry_run else "Screening backfill complete")
    for k, v in summary.items():
        print(f"  {k}: {v}")


def main():
    parser = argparse.ArgumentParser(prog="tenderizer")
    sub = parser.add_subparsers(dest="command", required=True)

    # CR-015 §3.4 — the bounded replacement for the run's old corpus-wide screening backfill
    sb = sub.add_parser("screen-backfill", help="Screen up to --limit tenders' missing answers (Claude calls).")
    sb.add_argument("--tenant", type=int, required=True, help="Tenant id.")
    sb.add_argument("--limit", type=int, required=True, help="Most tenders to screen in this pass.")
    sb.add_argument("--status", default=",".join(("shortlisted", "needs_review")),
                    help="Comma-separated tender statuses to include (default: shortlisted,needs_review).")
    sb.add_argument("--dry-run", action="store_true", help="Count what would be screened; make no calls.")
    sb.set_defaults(func=_screen_backfill)

    # CR-013 §2 — object-storage cutover (see storage_migration.py's docstring)
    sub.add_parser("storage-check", help="Verify the documents bucket credentials end to end."
                   ).set_defaults(func=_storage_check)
    mig = sub.add_parser("storage-migrate", help="Copy on-disk documents into the bucket (dry run by default).")
    mig.add_argument("--yes", action="store_true", help="Actually copy and update rows.")
    mig.set_defaults(func=_storage_migrate)
    rest = sub.add_parser("storage-restore-local", help="Rollback: copy bucket objects back to disk.")
    rest.add_argument("--yes", action="store_true", help="Actually download.")
    rest.set_defaults(func=_storage_restore_local)

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
