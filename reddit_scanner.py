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
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

import praw
import prawcore

from scrape_output import ScrapeOutput, ScrapeOutputError

SCRIPT_DIR = Path(__file__).resolve().parent
ENV_FILE = SCRIPT_DIR / ".env"
SCRAPES_DIR = SCRIPT_DIR / "scrapes"
DEFAULT_USER_AGENT = "reddit-csv-scraper/1.0"

ReturnType = TypeVar("ReturnType")

# Reddit free-tier Data API limit: 100 queries/min per OAuth client id,
# averaged over a rolling 10-minute window. prawcore already respects this
# proactively: it reads the x-ratelimit-remaining/used/reset headers on every
# response and sleeps *before* the next request, pausing fully when the quota
# is exhausted. The retry logic below is a safety net for the cases prawcore
# does not retry itself (429s, and 5xx/network errors past its 3 attempts).
MAX_RETRIES = 8
BASE_WAIT = 5  # seconds; doubles on every retry
MAX_WAIT = 300  # cap for a single backoff sleep


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


def call_with_retries(
    func: Callable[..., ReturnType], *args: Any, **kwargs: Any
) -> ReturnType:
    """Call a Reddit API operation with bounded exponential backoff."""
    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            return func(*args, **kwargs)
        except prawcore.exceptions.TooManyRequests as exc:
            last_error = exc
            # Reddit tells us how long to wait via the Retry-After header,
            # which prawcore exposes as exc.retry_after (may be absent).
            if exc.retry_after is not None:
                wait = float(exc.retry_after) + 2
            else:
                wait = min(BASE_WAIT * 2**attempt, MAX_WAIT)
            print(f"  Rate limited (429), sleeping {wait:.0f}s...")
            time.sleep(wait)
        except (
            prawcore.exceptions.ServerError,
            prawcore.exceptions.RequestException,
        ) as exc:
            last_error = exc
            wait = min(BASE_WAIT * 2**attempt, MAX_WAIT)
            print(f"  {type(exc).__name__}: {exc} — retrying in {wait:.0f}s...")
            time.sleep(wait)

    raise RuntimeError(f"giving up after {MAX_RETRIES} attempts") from last_error


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


def collect_rows(submission: praw.models.Submission) -> list[dict[str, Any]]:
    sub_info = {
        "submission_id": submission.id,
        "submission_author": submission.author.name if submission.author else "[deleted]",
        "submission_title": submission.title,
        "submission_created": fmt_ts(submission.created_utc),
        "submission_flair": submission.link_flair_text,
        "submission_over_18": submission.over_18,
        "submission_num_comments": submission.num_comments,
        "submission_url": submission.url,
    }

    call_with_retries(submission.comments.replace_more, limit=None)
    comments = submission.comments.list()

    if not comments:
        # Keep zero-comment submissions too, with empty comment fields.
        return [
            dict(
                sub_info,
                comment_id="",
                comment_author="",
                comment_body="",
                comment_created="",
                comment_score="",
            )
        ]

    return [
        dict(
            sub_info,
            comment_id=c.id,
            comment_author=c.author.name if c.author else "[deleted]",
            comment_body=c.body,
            comment_created=fmt_ts(c.created_utc),
            comment_score=c.score,
        )
        for c in comments
    ]


def scrape(
    reddit: praw.Reddit,
    subreddit_name: str,
    start_ts: float | None,
    end_ts: float | None,
    output: ScrapeOutput,
) -> None:
    subreddit = reddit.subreddit(subreddit_name)
    stream = subreddit.new(limit=None)

    resuming = output.completed_count > 0
    processed = skipped = failed = rows_written = 0
    oldest_seen = None
    reached_start = False

    try:
        while True:
            try:
                submission = call_with_retries(next, stream)
            except StopIteration:
                break

            created = submission.created_utc
            oldest_seen = created

            if end_ts is not None and created >= end_ts:
                continue
            if start_ts is not None and created < start_ts:
                reached_start = True
                break
            if output.has_submission(submission.id):
                skipped += 1
                continue

            try:
                rows = collect_rows(submission)
            except (RuntimeError, prawcore.PrawcoreException) as exc:
                failed += 1
                print(f"  Skipping submission {submission.id} ({exc})")
                continue

            output.save_submission(rows)
            rows_written += len(rows)
            processed += 1

            if processed % 25 == 0:
                remaining = reddit.auth.limits.get("remaining")
                quota = f", API quota left: {remaining:.0f}" if remaining is not None else ""
                print(
                    f"  {processed} submissions, {rows_written} rows "
                    f"— now at {fmt_ts(created)[:10]}{quota}"
                )
    except KeyboardInterrupt:
        print("\nInterrupted — all progress is saved. Re-run the same command to resume.")

    print(f"\nDone: {processed} submissions, {rows_written} rows written to {output}")
    if resuming and skipped:
        print(f"Skipped {skipped} submissions already present in the file (resume).")
    if failed:
        print(f"Failed to fetch {failed} submissions after repeated retries.")
    if (
        start_ts is not None
        and not reached_start
        and oldest_seen is not None
        and oldest_seen > start_ts
    ):
        print(
            f"Warning: Reddit's API only exposes the ~1000 newest posts; "
            f"the oldest reachable post is from {fmt_ts(oldest_seen)[:10]}, "
            f"so the range before that date could not be covered."
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
    try:
        call_with_retries(lambda: reddit.subreddit(subreddit).created_utc)
    except (RuntimeError, prawcore.PrawcoreException) as exc:
        sys.exit(f"Could not reach Reddit / invalid credentials: {exc}")

    try:
        with ScrapeOutput(output_path) as output:
            if output.completed_count:
                print(
                    f"Resuming: {output_path} already has "
                    f"{output.completed_count} submissions; they will be skipped."
                )

            print(
                f"Scraping r/{subreddit} from {start or 'the beginning'} to {end} "
                f"(read-only API, no login) -> {output_path}"
            )
            started = time.monotonic()
            scrape(reddit, subreddit, start_ts, end_ts, output)
            print(f"Elapsed: {time.monotonic() - started:.0f}s")
    except ScrapeOutputError as exc:
        sys.exit(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
