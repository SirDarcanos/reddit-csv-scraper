"""CSV persistence for resumable scrape output."""

import csv
import io
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import TracebackType
from typing import Any, TextIO

FIELDNAMES = [
    "submission_id",
    "submission_author",
    "submission_title",
    "submission_created",
    "submission_flair",
    "submission_over_18",
    "submission_num_comments",
    "submission_url",
    "comment_id",
    "comment_author",
    "comment_body",
    "comment_created",
    "comment_score",
]


class ScrapeOutputError(RuntimeError):
    """Report a scrape output failure with its file context."""


class ScrapeOutput:
    """Own the lifecycle and resume state of one scrape output file."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._file: TextIO | None = None
        self._completed_ids: set[str] = set()

    def __str__(self) -> str:
        return str(self._path)

    @property
    def completed_count(self) -> int:
        return len(self._completed_ids)

    def has_submission(self, submission_id: str) -> bool:
        return submission_id in self._completed_ids

    def save_submission(self, rows: Sequence[Mapping[str, Any]]) -> None:
        if self._file is None:
            raise ScrapeOutputError(f"Scrape output {self._path} is not open")
        if not rows:
            raise ScrapeOutputError(
                f"Scrape output {self._path} cannot save an empty submission batch"
            )

        expected_fields = set(FIELDNAMES)
        if any(set(row) != expected_fields for row in rows):
            raise ScrapeOutputError(
                f"Scrape output {self._path} received a row with an incompatible schema"
            )

        submission_id = rows[0]["submission_id"]
        if (
            not isinstance(submission_id, str)
            or not submission_id
            or any(row["submission_id"] != submission_id for row in rows[1:])
        ):
            raise ScrapeOutputError(
                f"Scrape output {self._path} requires one non-empty "
                "submission ID per batch"
            )
        if submission_id in self._completed_ids:
            raise ScrapeOutputError(
                f"Scrape output {self._path} already contains "
                f"submission {submission_id}"
            )

        buffer = io.StringIO(newline="")
        try:
            csv.DictWriter(buffer, fieldnames=FIELDNAMES).writerows(rows)
        except (csv.Error, TypeError, UnicodeError, ValueError) as exc:
            raise ScrapeOutputError(
                f"Could not serialize submission {submission_id} for scrape output "
                f"{self._path}: {exc}"
            ) from exc

        try:
            self._file.write(buffer.getvalue())
            self._file.flush()
        except (OSError, UnicodeError) as exc:
            raise ScrapeOutputError(
                f"Could not write submission {submission_id} to scrape output "
                f"{self._path}: {exc}"
            ) from exc
        self._completed_ids.add(submission_id)

    def __enter__(self) -> "ScrapeOutput":
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fresh = not self._path.exists() or self._path.stat().st_size == 0
            if not fresh:
                with self._path.open(newline="", encoding="utf-8") as file:
                    reader = csv.reader(file)
                    if next(reader, None) != FIELDNAMES:
                        raise ScrapeOutputError(
                            f"Scrape output {self._path} has an incompatible CSV header"
                        )
                    for line_number, row in enumerate(reader, start=2):
                        if len(row) != len(FIELDNAMES) or not row[0]:
                            raise ScrapeOutputError(
                                f"Scrape output {self._path} has an invalid row "
                                f"at line {line_number}"
                            )
                        self._completed_ids.add(row[0])
            self._file = self._path.open("a", newline="", encoding="utf-8")
            if fresh:
                csv.DictWriter(self._file, fieldnames=FIELDNAMES).writeheader()
                self._file.flush()
            return self
        except ScrapeOutputError:
            raise
        except (csv.Error, OSError, UnicodeError) as exc:
            if self._file is not None:
                self._file.close()
                self._file = None
            raise ScrapeOutputError(
                f"Could not open scrape output {self._path}: {exc}"
            ) from exc

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._file is not None:
            file = self._file
            self._file = None
            try:
                file.close()
            except OSError as close_error:
                if exc_type is None:
                    raise ScrapeOutputError(
                        f"Could not close scrape output {self._path}: {close_error}"
                    ) from close_error
