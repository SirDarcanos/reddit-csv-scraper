#!/usr/bin/env python3
"""Scrape submissions and comments from a subreddit into a CSV.

Usage:
    ./reddit_scanner.py --subreddit Python --start 2026-07-01
    ./reddit_scanner.py --start 2026-07-01 --end 2026-08-01

The subreddit can be supplied with ``--subreddit`` or configured with the
``REDDIT_SUBREDDIT`` environment variable. Command-line arguments take
precedence. No login or 2FA is needed: public data is fetched in read-only
mode using credentials from the .env file next to this script or from
already-exported environment variables, which take precedence.

Progress is written to the CSV after every submission, so an interrupted
run loses nothing: re-run the same command and already-scraped submissions
are skipped automatically.

Note: Reddit's API only exposes roughly the 1000 newest posts of a
subreddit, so very old start dates may not be fully reachable.
"""

import argparse
import datetime as dt
import os
import sys
import time
from pathlib import Path

import praw

from scrape_output import ScrapeOutput, ScrapeOutputError
from scrape_run import (
    ProgressReported,
    RetryScheduled,
    RunStarted,
    ScrapeEvent,
    ScrapeRange,
    ScrapeValidationError,
    SubmissionFailed,
    run_scrape,
)

SCRIPT_DIR = Path(__file__).resolve().parent
ENV_FILE = SCRIPT_DIR / ".env"
SCRAPES_DIR = SCRIPT_DIR / "scrapes"
DEFAULT_USER_AGENT = "reddit-csv-scraper/1.0"

def fmt_ts(timestamp: float) -> str:
    """Format a Unix timestamp as a UTC date and time."""
    return dt.datetime.fromtimestamp(timestamp, dt.timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def parse_date(value: str) -> dt.datetime:
    """Parse a YYYY-MM-DD value at midnight UTC."""
    return dt.datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)


def prompt_date(label: str, hint: str) -> str | None:
    while True:
        raw = input(f"{label} (YYYY-MM-DD, {hint}): ").strip()
        if not raw:
            return None
        try:
            parse_date(raw)
            return raw
        except ValueError:
            print(f"  '{raw}' is not a valid YYYY-MM-DD date, try again.")


def load_dotenv(path: Path) -> None:
    """Load KEY=VALUE lines from a .env file without overriding exported vars."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def normalize_subreddit(value: str) -> str:
    """Return a bare subreddit name, accepting either ``name`` or ``r/name``."""
    value = value.strip()
    if value.lower().startswith("r/"):
        value = value[2:]
    if not value:
        raise ValueError("subreddit name cannot be empty")
    return value


def make_reddit() -> praw.Reddit:
    client_id = os.environ.get("REDDIT_CLIENT_ID")
    client_secret = os.environ.get("REDDIT_CLIENT_SECRET")
    user_agent = os.environ.get("REDDIT_USER_AGENT") or DEFAULT_USER_AGENT

    if not client_id or not client_secret:
        sys.exit(
            "Missing Reddit credentials: put REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET "
            "in the .env file next to this script, or export them as environment variables."
        )

    return praw.Reddit(
        client_id=client_id,
        client_secret=client_secret,
        user_agent=user_agent,
        ratelimit_seconds=600,
    )


def terminal_reporter(
    event: ScrapeEvent,
    output_path: Path,
    subreddit: str,
    start: str | None,
    end: str,
) -> None:
    """Render Scrape run events with the CLI's established wording."""
    if isinstance(event, RetryScheduled):
        if event.rate_limited:
            print(f"  Rate limited (429), sleeping {event.wait_seconds:.0f}s...")
        else:
            print(
                f"  {event.error_type}: {event.error} "
                f"— retrying in {event.wait_seconds:.0f}s..."
            )
    elif isinstance(event, RunStarted):
        if event.prior_completion:
            print(
                f"Resuming: {output_path} already has "
                f"{event.prior_completion} submissions; they will be skipped."
            )
        print(
            f"Scraping r/{subreddit} from {start or 'the beginning'} to {end} "
            f"(read-only API, no login) -> {output_path}"
        )
    elif isinstance(event, SubmissionFailed):
        print(f"  Skipping submission {event.submission_id} ({event.error})")
    elif isinstance(event, ProgressReported):
        remaining = event.api_quota_remaining
        quota = f", API quota left: {remaining:.0f}" if remaining is not None else ""
        print(
            f"  {event.processed} submissions, {event.rows} rows "
            f"— now at {fmt_ts(event.submission_created)[:10]}{quota}"
        )


def main() -> int:
    load_dotenv(ENV_FILE)

    parser = argparse.ArgumentParser(
        description="Scrape subreddit submissions and comments into a CSV (no login needed)."
    )
    parser.add_argument(
        "--start",
        help="Oldest post date to include, YYYY-MM-DD (default: as far back as available)",
    )
    parser.add_argument(
        "--end",
        help="Newest post date to include, YYYY-MM-DD (default: today)",
    )
    parser.add_argument(
        "--subreddit",
        help="Subreddit name (or set REDDIT_SUBREDDIT in the environment or .env)",
    )
    parser.add_argument(
        "--output",
        help="Output CSV path (default: scrapes/topics_with_comments_<start>_to_<end>.csv)",
    )
    args = parser.parse_args()

    subreddit_value = args.subreddit or os.environ.get("REDDIT_SUBREDDIT")
    if not subreddit_value:
        parser.error("a subreddit is required: use --subreddit or set REDDIT_SUBREDDIT")
    try:
        subreddit = normalize_subreddit(subreddit_value)
    except ValueError as exc:
        parser.error(str(exc))

    start, end = args.start, args.end
    if sys.stdin.isatty():
        if start is None:
            start = prompt_date("Start date", "empty = as far back as Reddit allows")
        if end is None:
            end = prompt_date("End date", "empty = today")

    today = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    end = end or today

    try:
        start_dt = parse_date(start) if start else None
        end_dt = parse_date(end)
    except ValueError as exc:
        sys.exit(f"Invalid date: {exc}")

    if start_dt is not None and start_dt > end_dt:
        sys.exit(f"Start date {start} is after end date {end}.")

    start_ts = start_dt.timestamp() if start_dt else None
    # Include the whole end day.
    end_ts = (end_dt + dt.timedelta(days=1)).timestamp()

    default_name = f"topics_with_comments_{start or 'all'}_to_{end}.csv"
    output_path = (
        Path(args.output).expanduser() if args.output else SCRAPES_DIR / default_name
    )

    reddit = make_reddit()
    started: float | None = None

    def report_and_track_start(event: ScrapeEvent) -> None:
        """Render an event and start elapsed timing when traversal begins."""
        nonlocal started
        if isinstance(event, RunStarted):
            started = time.monotonic()
        terminal_reporter(event, output_path, subreddit, start, end)

    try:
        outcome = run_scrape(
            reddit,
            subreddit,
            ScrapeRange(start=start_ts, end=end_ts),
            ScrapeOutput(output_path),
            report_and_track_start,
        )
    except ScrapeValidationError as exc:
        sys.exit(f"Could not reach Reddit / invalid credentials: {exc}")
    except ScrapeOutputError as exc:
        sys.exit(str(exc))

    if outcome.interrupted:
        print("\nInterrupted — all progress is saved. Re-run the same command to resume.")
    print(
        f"\nDone: {outcome.processed} submissions, {outcome.rows} rows "
        f"written to {output_path}"
    )
    if outcome.prior_completion and outcome.skipped:
        print(
            f"Skipped {outcome.skipped} submissions already present in the file "
            "(resume)."
        )
    if outcome.failed:
        print(f"Failed to fetch {outcome.failed} submissions after repeated retries.")
    if outcome.incomplete_range and outcome.oldest_seen is not None:
        print(
            "Warning: Reddit's API only exposes the ~1000 newest posts; "
            f"the oldest reachable post is from {fmt_ts(outcome.oldest_seen)[:10]}, "
            "so the range before that date could not be covered."
        )
    if started is not None:
        print(f"Elapsed: {time.monotonic() - started:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
