"""Command-line entry point (``vishwas`` console script).

    vishwas serve                      # API + dashboard on http://127.0.0.1:8000
    vishwas serve --port 9000 --reload

The evaluation suite is run with ``python scripts/run_evaluation.py``.
"""

from __future__ import annotations

import argparse


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="vishwas", description="VISHWAS swarm-integrity server")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run the API and dashboard")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true", help="restart on source edits")

    args = parser.parse_args(argv)

    if args.command == "serve":
        import uvicorn

        uvicorn.run(
            "vishwas.api.app:app",
            host=args.host,
            port=args.port,
            reload=args.reload,
        )


if __name__ == "__main__":
    main()
