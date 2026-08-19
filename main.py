"""
Congress Text Analysis - CLI Entrypoint & Launcher.
"""

import argparse
from pathlib import Path
import subprocess
import sys


def launch_app():
    """Launch the Streamlit web application."""
    app_path = Path(__file__).resolve().parent / "app" / "app.py"
    cmd = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(app_path),
        "--server.headless",
        "true",
    ]
    print("Starting Congressional Record Explorer Web Interface...")
    subprocess.run(cmd)


def run_process(args):
    """Run data ingestion pipeline."""
    from scripts import process_corpus

    project_root = Path(__file__).resolve().parent
    out_dir = project_root / args.out_dir

    if args.congress:
        process_corpus.process_single_congress(
            args.congress, out_dir, force=args.force
        )
    elif args.all:
        process_corpus.process_range(43, 114, out_dir, force=args.force)
    else:
        process_corpus.process_range(
            args.start, args.end, out_dir, force=args.force
        )


def run_inspect(args):
    """Run archive inspector."""
    from scripts import inspect_zip

    inspect_zip.inspect_congress(args.congress, args.lines)


def run_cli_search(query: str, limit: int = 5):
    """Quick CLI search using the DuckDB engine."""
    from app.search_engine import SearchEngine, SearchFilter

    engine = SearchEngine()
    sf = SearchFilter(query=query, limit=limit)
    df, count, _ = engine.search(sf)

    print(f"\nSearch results for '{query}' (Found {count:,} total matches):")
    print("=" * 70)
    for _, row in df.iterrows():
        sp = row.get("speaker") or f"{row.get('first_name')} {row.get('last_name')}"
        date = row.get("date")
        chamber = row.get("chamber")
        party = row.get("party") or "N/A"
        state = row.get("state") or "N/A"
        text = row.get("speech_text") or ""
        print(f"[{date}] {sp} ({party}-{state}, {chamber})")
        print(f"  {text[:200]}...")
        print("-" * 70)


def main():
    parser = argparse.ArgumentParser(
        description="Congressional Record Analysis & Explorer Suite"
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # App command
    subparsers.add_parser("app", help="Launch Streamlit web application")

    # Ingest / Process command
    proc_parser = subparsers.add_parser(
        "process", help="Process raw ZIP files into Parquet"
    )
    proc_parser.add_argument(
        "--congress", "-c", type=int, help="Single Congress number (43-114)"
    )
    proc_parser.add_argument(
        "--start", type=int, default=43, help="Start Congress (default: 43)"
    )
    proc_parser.add_argument(
        "--end", type=int, default=114, help="End Congress (default: 114)"
    )
    proc_parser.add_argument(
        "--all", action="store_true", help="Process all 72 Congresses (43-114)"
    )
    proc_parser.add_argument(
        "--force", "-f", action="store_true", help="Overwrite existing parquet"
    )
    proc_parser.add_argument(
        "--out-dir",
        "-o",
        type=str,
        default="data/processed",
        help="Parquet output directory",
    )

    # Inspect command
    insp_parser = subparsers.add_parser(
        "inspect", help="Inspect raw zip files without unzipping"
    )
    insp_parser.add_argument(
        "--congress",
        "-c",
        type=int,
        default=43,
        help="Congress number to inspect",
    )
    insp_parser.add_argument(
        "--lines",
        "-n",
        type=int,
        default=5,
        help="Number of preview lines",
    )

    # Search command
    search_parser = subparsers.add_parser(
        "search", help="Search processed speeches directly from terminal"
    )
    search_parser.add_argument("query", type=str, help="Search query or phrase")
    search_parser.add_argument(
        "--limit", "-l", type=int, default=5, help="Number of results to show"
    )

    args = parser.parse_args()

    if args.command == "app" or len(sys.argv) == 1:
        launch_app()
    elif args.command == "process":
        run_process(args)
    elif args.command == "inspect":
        run_inspect(args)
    elif args.command == "search":
        run_cli_search(args.query, args.limit)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
