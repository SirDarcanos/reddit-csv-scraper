"""Orchestrate one resumable Reddit scrape run."""

import datetime as dt
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

import prawcore

from scrape_output import ScrapeOutput

ReturnType = TypeVar("ReturnType")

MAX_RETRIES = 8
BASE_WAIT = 5
MAX_WAIT = 300


@dataclass(frozen=True)
class ScrapeRange:
    """A validated inclusive-start, exclusive-end timestamp range."""

    start: float | None
    end: float | None


@dataclass(frozen=True)
class RetryScheduled:
    """A transient Reddit failure will be retried after a delay."""

    error_type: str
    error: str
    wait_seconds: float
    rate_limited: bool


@dataclass(frozen=True)
class RunStarted:
    """The Scrape output is open and traversal is about to start."""

    prior_completion: int


@dataclass(frozen=True)
class SubmissionFailed:
    """Comment retrieval was exhausted for one submission."""

    submission_id: str
    error: str


@dataclass(frozen=True)
class ProgressReported:
    """Periodic saved-submission progress for a running scrape."""

    processed: int
    rows: int
    submission_created: float
    api_quota_remaining: float | None


ScrapeEvent = RetryScheduled | RunStarted | SubmissionFailed | ProgressReported
EventReporter = Callable[[ScrapeEvent], None]


class ScrapeValidationError(RuntimeError):
    """Reddit access could not be validated before opening the output."""


@dataclass(frozen=True)
class ScrapeOutcome:
    """Accounting and coverage state returned by a completed Scrape run."""

    processed: int
    skipped: int
    failed: int
    rows: int
    interrupted: bool
    prior_completion: int
    incomplete_range: bool
    oldest_seen: float | None


def _call_with_retries(
    reporter: EventReporter,
    func: Callable[..., ReturnType],
    *args: Any,
    **kwargs: Any,
) -> ReturnType:
    """Retry transient Reddit failures and report each scheduled delay."""
    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            return func(*args, **kwargs)
        except prawcore.exceptions.TooManyRequests as exc:
            last_error = exc
            retry_after = getattr(exc, "retry_after", None)
            wait = (
                float(retry_after) + 2
                if retry_after is not None
                else min(BASE_WAIT * 2**attempt, MAX_WAIT)
            )
            reporter(
                RetryScheduled(
                    error_type=type(exc).__name__,
                    error=str(exc),
                    wait_seconds=wait,
                    rate_limited=True,
                )
            )
            time.sleep(wait)
        except (
            prawcore.exceptions.ServerError,
            prawcore.exceptions.RequestException,
        ) as exc:
            last_error = exc
            wait = min(BASE_WAIT * 2**attempt, MAX_WAIT)
            reporter(
                RetryScheduled(
                    error_type=type(exc).__name__,
                    error=str(exc),
                    wait_seconds=wait,
                    rate_limited=False,
                )
            )
            time.sleep(wait)

    raise RuntimeError(f"giving up after {MAX_RETRIES} attempts") from last_error


def _format_timestamp(timestamp: float) -> str:
    return dt.datetime.fromtimestamp(timestamp, dt.timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def _collect_rows(
    submission: Any, reporter: EventReporter
) -> list[dict[str, Any]]:
    """Build CSV rows, including one placeholder row when comments are empty."""
    submission_info = {
        "submission_id": submission.id,
        "submission_author": (
            submission.author.name if submission.author else "[deleted]"
        ),
        "submission_title": submission.title,
        "submission_created": _format_timestamp(submission.created_utc),
        "submission_flair": submission.link_flair_text,
        "submission_over_18": submission.over_18,
        "submission_num_comments": submission.num_comments,
        "submission_url": submission.url,
    }

    _call_with_retries(reporter, submission.comments.replace_more, limit=None)
    comments = submission.comments.list()
    if not comments:
        return [
            dict(
                submission_info,
                comment_id="",
                comment_author="",
                comment_body="",
                comment_created="",
                comment_score="",
            )
        ]

    return [
        dict(
            submission_info,
            comment_id=comment.id,
            comment_author=(comment.author.name if comment.author else "[deleted]"),
            comment_body=comment.body,
            comment_created=_format_timestamp(comment.created_utc),
            comment_score=comment.score,
        )
        for comment in comments
    ]


def run_scrape(
    reddit: Any,
    subreddit_name: str,
    timestamp_range: ScrapeRange,
    output: ScrapeOutput,
    reporter: EventReporter,
) -> ScrapeOutcome:
    """Validate access, open output, traverse Reddit, and return run accounting.

    ``subreddit_name`` and ``timestamp_range`` must already be normalized and
    validated. ``output`` must not be open; this function owns its lifecycle.
    Reddit listing and Scrape output failures are fatal. Exhausted comment
    retrieval is reported and skipped so later submissions can still be saved.
    """
    subreddit = reddit.subreddit(subreddit_name)
    try:
        _call_with_retries(reporter, lambda: subreddit.created_utc)
    except (RuntimeError, prawcore.PrawcoreException) as exc:
        raise ScrapeValidationError(str(exc)) from exc

    processed = skipped = failed = rows_written = 0
    oldest_seen: float | None = None
    reached_start = False
    interrupted = False

    with output:
        prior_completion = output.completed_count
        reporter(RunStarted(prior_completion=prior_completion))
        stream = subreddit.new(limit=None)

        try:
            while True:
                try:
                    submission = _call_with_retries(reporter, next, stream)
                except StopIteration:
                    break

                created = submission.created_utc
                oldest_seen = created

                if timestamp_range.end is not None and created >= timestamp_range.end:
                    continue
                if (
                    timestamp_range.start is not None
                    and created < timestamp_range.start
                ):
                    reached_start = True
                    break
                if output.has_submission(submission.id):
                    skipped += 1
                    continue

                try:
                    rows = _collect_rows(submission, reporter)
                except (RuntimeError, prawcore.PrawcoreException) as exc:
                    failed += 1
                    reporter(SubmissionFailed(submission.id, str(exc)))
                    continue

                output.save_submission(rows)
                processed += 1
                rows_written += len(rows)

                if processed % 25 == 0:
                    reporter(
                        ProgressReported(
                            processed=processed,
                            rows=rows_written,
                            submission_created=created,
                            api_quota_remaining=reddit.auth.limits.get("remaining"),
                        )
                    )
        except KeyboardInterrupt:
            interrupted = True

    incomplete_range = bool(
        not interrupted
        and timestamp_range.start is not None
        and not reached_start
        and oldest_seen is not None
        and oldest_seen > timestamp_range.start
    )
    return ScrapeOutcome(
        processed=processed,
        skipped=skipped,
        failed=failed,
        rows=rows_written,
        interrupted=interrupted,
        prior_completion=prior_completion,
        incomplete_range=incomplete_range,
        oldest_seen=oldest_seen,
    )
