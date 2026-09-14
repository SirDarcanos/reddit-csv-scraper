import csv
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import patch

import prawcore

from scrape_output import ScrapeOutput, ScrapeOutputError
from scrape_run import (
    ProgressReported,
    RetryScheduled,
    RunStarted,
    ScrapeRange,
    SubmissionFailed,
    run_scrape,
)


class FakeAuthor:
    def __init__(self, name: str) -> None:
        self.name = name


class FakeComment:
    def __init__(self, comment_id: str, body: str = "Comment") -> None:
        self.id = comment_id
        self.author = FakeAuthor("commenter")
        self.body = body
        self.created_utc = 150.0
        self.score = 2


class FakeComments:
    def __init__(
        self,
        comments: list[FakeComment] | None = None,
        replace_errors: list[Exception] | None = None,
    ) -> None:
        self._comments = comments or []
        self._replace_errors = replace_errors or []

    def replace_more(self, limit: None = None) -> None:
        if self._replace_errors:
            raise self._replace_errors.pop(0)

    def list(self) -> list[FakeComment]:
        return self._comments


class FakeSubmission:
    def __init__(
        self,
        submission_id: str,
        created_utc: float,
        comments: FakeComments | None = None,
    ) -> None:
        self.id = submission_id
        self.created_utc = created_utc
        self.author = FakeAuthor("submitter")
        self.title = f"Title {submission_id}"
        self.link_flair_text = None
        self.over_18 = False
        self.num_comments = len(comments._comments) if comments else 0
        self.url = f"https://example.com/{submission_id}"
        self.comments = comments or FakeComments()


class FakeListing:
    def __init__(self, submissions: list[FakeSubmission]) -> None:
        self._submissions = iter(submissions)

    def __iter__(self) -> "FakeListing":
        return self

    def __next__(self) -> FakeSubmission:
        return next(self._submissions)


class FakeSubreddit:
    def __init__(
        self,
        submissions: list[FakeSubmission],
        validation_error: Exception | None = None,
    ) -> None:
        self._submissions = submissions
        self._validation_error = validation_error

    @property
    def created_utc(self) -> float:
        if self._validation_error is not None:
            raise self._validation_error
        return 1.0

    def new(self, limit: None = None) -> FakeListing:
        return FakeListing(self._submissions)


class FakeReddit:
    def __init__(self, subreddit: FakeSubreddit, remaining: float | None = 42) -> None:
        self._subreddit = subreddit
        self.requested_names: list[str] = []
        self.auth = type("Auth", (), {"limits": {"remaining": remaining}})()

    def subreddit(self, name: str) -> FakeSubreddit:
        self.requested_names.append(name)
        return self._subreddit


def request_error(message: str) -> prawcore.exceptions.RequestException:
    return prawcore.exceptions.RequestException(Exception(message), (), {})


class ScrapeRunTests(unittest.TestCase):
    def test_run_validates_reddit_before_opening_output_and_emits_start(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "output.csv"
            reddit = FakeReddit(FakeSubreddit([]))
            events: list[object] = []

            outcome = run_scrape(
                reddit,
                "Python",
                ScrapeRange(start=100.0, end=200.0),
                ScrapeOutput(path),
                events.append,
            )

            self.assertTrue(path.exists())
            self.assertEqual(reddit.requested_names, ["Python"])
            self.assertEqual(events, [RunStarted(prior_completion=0)])
            self.assertEqual(outcome.processed, 0)
            self.assertEqual(outcome.rows, 0)
            self.assertFalse(outcome.interrupted)

    def test_failed_validation_does_not_create_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "output.csv"
            reddit = FakeReddit(
                FakeSubreddit([], validation_error=request_error("offline"))
            )

            with patch("scrape_run.time.sleep"):
                with self.assertRaises(RuntimeError):
                    run_scrape(
                        reddit,
                        "Python",
                        ScrapeRange(start=None, end=200.0),
                        ScrapeOutput(path),
                        lambda event: None,
                    )

            self.assertFalse(path.exists())

    def test_run_selects_range_and_saves_zero_comment_submission(self) -> None:
        submissions = [
            FakeSubmission("at-end", 200.0),
            FakeSubmission("inside", 150.0),
            FakeSubmission("at-start", 100.0),
            FakeSubmission("before", 99.0),
            FakeSubmission("never-visited", 98.0),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "output.csv"

            outcome = run_scrape(
                FakeReddit(FakeSubreddit(submissions)),
                "Python",
                ScrapeRange(start=100.0, end=200.0),
                ScrapeOutput(path),
                lambda event: None,
            )

            with path.open(newline="", encoding="utf-8") as file:
                rows = list(csv.DictReader(file))
            self.assertEqual(
                [row["submission_id"] for row in rows], ["inside", "at-start"]
            )
            self.assertEqual(rows[0]["comment_id"], "")
            self.assertEqual(outcome.processed, 2)
            self.assertEqual(outcome.rows, 2)
            self.assertFalse(outcome.incomplete_range)

    def test_run_resumes_existing_output_and_reports_prior_completion(self) -> None:
        submissions = [
            FakeSubmission("new", 150.0),
            FakeSubmission("done", 140.0),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "output.csv"
            with ScrapeOutput(path) as output:
                output.save_submission(
                    [
                        {
                            "submission_id": "done",
                            "submission_author": "author",
                            "submission_title": "Done",
                            "submission_created": "1970-01-01 00:02:20",
                            "submission_flair": None,
                            "submission_over_18": False,
                            "submission_num_comments": 0,
                            "submission_url": "https://example.com/done",
                            "comment_id": "",
                            "comment_author": "",
                            "comment_body": "",
                            "comment_created": "",
                            "comment_score": "",
                        }
                    ]
                )
            events: list[object] = []

            outcome = run_scrape(
                FakeReddit(FakeSubreddit(submissions)),
                "Python",
                ScrapeRange(start=None, end=200.0),
                ScrapeOutput(path),
                events.append,
            )

            self.assertEqual(events[0], RunStarted(prior_completion=1))
            self.assertEqual(outcome.prior_completion, 1)
            self.assertEqual(outcome.processed, 1)
            self.assertEqual(outcome.skipped, 1)

    def test_comment_failure_emits_event_and_continues(self) -> None:
        failed = FakeSubmission(
            "failed",
            160.0,
            FakeComments(replace_errors=[request_error("comments unavailable")] * 8),
        )
        saved = FakeSubmission("saved", 150.0, FakeComments([FakeComment("c1")]))
        events: list[object] = []
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "output.csv"

            with patch("scrape_run.time.sleep"):
                outcome = run_scrape(
                    FakeReddit(FakeSubreddit([failed, saved])),
                    "Python",
                    ScrapeRange(start=None, end=200.0),
                    ScrapeOutput(path),
                    events.append,
                )

            retries = [event for event in events if isinstance(event, RetryScheduled)]
            failures = [
                event for event in events if isinstance(event, SubmissionFailed)
            ]
            self.assertEqual(len(retries), 8)
            self.assertEqual(failures[0].submission_id, "failed")
            self.assertEqual(outcome.failed, 1)
            self.assertEqual(outcome.processed, 1)
            self.assertEqual(outcome.rows, 1)

    def test_progress_is_emitted_every_25_saved_submissions(self) -> None:
        submissions = [
            FakeSubmission(f"submission-{number}", float(200 - number))
            for number in range(1, 52)
        ]
        events: list[object] = []
        with tempfile.TemporaryDirectory() as directory:
            outcome = run_scrape(
                FakeReddit(FakeSubreddit(submissions), remaining=37),
                "Python",
                ScrapeRange(start=None, end=200.0),
                ScrapeOutput(Path(directory) / "output.csv"),
                events.append,
            )

        progress = [event for event in events if isinstance(event, ProgressReported)]
        self.assertEqual([event.processed for event in progress], [25, 50])
        self.assertEqual([event.rows for event in progress], [25, 50])
        self.assertEqual([event.api_quota_remaining for event in progress], [37, 37])
        self.assertEqual(outcome.processed, 51)

    def test_interruption_returns_resumable_outcome_without_range_claim(self) -> None:
        class InterruptingListing(FakeListing):
            def __next__(self) -> FakeSubmission:
                submission = super().__next__()
                if submission.id == "interrupt":
                    raise KeyboardInterrupt
                return submission

        class InterruptingSubreddit(FakeSubreddit):
            def new(self, limit: None = None) -> InterruptingListing:
                return InterruptingListing(self._submissions)

        submissions = [
            FakeSubmission("saved", 150.0),
            FakeSubmission("interrupt", 140.0),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "output.csv"
            outcome = run_scrape(
                FakeReddit(InterruptingSubreddit(submissions)),
                "Python",
                ScrapeRange(start=100.0, end=200.0),
                ScrapeOutput(path),
                lambda event: None,
            )

            with path.open(newline="", encoding="utf-8") as file:
                rows = list(csv.DictReader(file))
            self.assertEqual([row["submission_id"] for row in rows], ["saved"])
            self.assertTrue(outcome.interrupted)
            self.assertFalse(outcome.incomplete_range)

    def test_listing_failure_remains_fatal(self) -> None:
        class FailingListing(FakeListing):
            def __next__(self) -> FakeSubmission:
                raise request_error("listing unavailable")

        class FailingSubreddit(FakeSubreddit):
            def new(self, limit: None = None) -> FailingListing:
                return FailingListing([])

        with tempfile.TemporaryDirectory() as directory:
            with patch("scrape_run.time.sleep"):
                with self.assertRaises(RuntimeError):
                    run_scrape(
                        FakeReddit(FailingSubreddit([])),
                        "Python",
                        ScrapeRange(start=None, end=200.0),
                        ScrapeOutput(Path(directory) / "output.csv"),
                        lambda event: None,
                    )

    def test_scrape_output_failure_remains_fatal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "output.csv"
            path.write_text("wrong,header\n", encoding="utf-8")

            with self.assertRaises(ScrapeOutputError):
                run_scrape(
                    FakeReddit(FakeSubreddit([])),
                    "Python",
                    ScrapeRange(start=None, end=200.0),
                    ScrapeOutput(path),
                    lambda event: None,
                )

    def test_events_are_immutable(self) -> None:
        event = RunStarted(prior_completion=0)

        with self.assertRaises(FrozenInstanceError):
            event.prior_completion = 1  # type: ignore[misc]

    def test_exhausted_listing_reports_incomplete_requested_range(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            outcome = run_scrape(
                FakeReddit(FakeSubreddit([FakeSubmission("oldest", 150.0)])),
                "Python",
                ScrapeRange(start=100.0, end=200.0),
                ScrapeOutput(Path(directory) / "output.csv"),
                lambda event: None,
            )

        self.assertTrue(outcome.incomplete_range)
        self.assertEqual(outcome.oldest_seen, 150.0)


if __name__ == "__main__":
    unittest.main()
