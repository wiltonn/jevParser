"""``jev-web``: run and administer the web app."""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    from .config import get_settings

    parser = argparse.ArgumentParser(prog="jev-web")
    sub = parser.add_subparsers(dest="command", required=True)

    s = sub.add_parser("serve", help="run the web app")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--reload", action="store_true")

    sub.add_parser("upgrade", help="apply database migrations")
    sub.add_parser("seed", help="create the local admin, sample catalog and GM ruleset")
    imp = sub.add_parser("import-cache", help="load a CLI judgments.json into the database")
    imp.add_argument("path", nargs="?", default=".jev-cache/judgments.json")
    w = sub.add_parser("worker", help="process jobs without serving HTTP")
    w.add_argument("--once", action="store_true", help="drain the queue and exit")
    rev = sub.add_parser("revision", help="autogenerate a migration (development)")
    rev.add_argument("message")

    args = parser.parse_args(argv)
    settings = get_settings()

    if args.command == "serve":
        import uvicorn

        uvicorn.run("jev_web.app:create_app", factory=True, host=args.host, port=args.port,
                    reload=args.reload)
        return 0

    from . import migrate
    from .db import configure, session_scope

    if args.command == "revision":
        migrate.revision(settings.db_url, args.message)
        return 0

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    migrate.upgrade(settings.db_url)
    configure(settings.db_url)
    if args.command == "upgrade":
        print(f"database at head: {settings.db_url}")
    elif args.command == "seed":
        from .services.seed import seed

        with session_scope() as session:
            print(seed(session))
    elif args.command == "import-cache":
        from .services.judgment_store import import_json_cache

        with session_scope() as session:
            added, present = import_json_cache(session, args.path)
        print(f"imported {added} judgments ({present} already present)")
    elif args.command == "worker":
        from .jobs.worker import WorkerPool, run_pending

        if args.once:
            print(f"ran {run_pending()} job(s)")
        else:
            pool = WorkerPool(max(1, settings.workers))
            pool.start()
            try:
                import threading

                threading.Event().wait()
            except KeyboardInterrupt:
                pool.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
