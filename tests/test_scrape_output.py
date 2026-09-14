import csv
import tempfile
import unittest
from pathlib import Path

from scrape_output import ScrapeOutput, ScrapeOutputError


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


def make_row(
    submission_id: str = "abc", comment_id: str = "comment-1"
) -> dict[str, object]:
    return {
        "submission_id": submission_id,
        "submission_author": "author",
        "submission_title": "Title",
        "submission_created": "2026-09-14 12:00:00",
        "submission_flair": None,
        "submission_over_18": False,
        "submission_num_comments": 1,
        "submission_url": "https://example.com/post",
        "comment_id": comment_id,
        "comment_author": "commenter",
        "comment_body": "Comment",
        "comment_created": "2026-09-14 12:01:00",
        "comment_score": 2,
    }


class ScrapeOutputTests(unittest.TestCase):
    def test_opening_fresh_output_creates_header_and_parent_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "output.csv"

            with ScrapeOutput(path) as output:
                self.assertEqual(output.completed_count, 0)

            self.assertEqual(path.read_text().splitlines()[0], ",".join(FIELDNAMES))

    def test_opening_valid_output_recovers_completed_submissions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "output.csv"
            with path.open("w", newline="", encoding="utf-8") as file:
                writer = csv.DictWriter(file, fieldnames=FIELDNAMES)
                writer.writeheader()
                writer.writerow(make_row("first"))
                writer.writerow(make_row("second"))
                writer.writerow(make_row("first", "comment-2"))

            with ScrapeOutput(path) as output:
                self.assertEqual(output.completed_count, 2)
                self.assertTrue(output.has_submission("first"))
                self.assertTrue(output.has_submission("second"))
                self.assertFalse(output.has_submission("missing"))
                output.save_submission([make_row("third")])

            with path.open(newline="", encoding="utf-8") as file:
                self.assertEqual(
                    [row["submission_id"] for row in csv.DictReader(file)],
                    ["first", "second", "first", "third"],
                )

    def test_opening_output_with_incompatible_header_fails_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "output.csv"
            original = "submission_id,unexpected\nabc,value\n"
            path.write_text(original)

            with self.assertRaisesRegex(ScrapeOutputError, str(path)):
                with ScrapeOutput(path):
                    pass

            self.assertEqual(path.read_text(), original)

    def test_save_submission_appends_one_flushed_batch_and_updates_state(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "output.csv"
            rows = [make_row("abc", "one"), make_row("abc", "two")]

            with ScrapeOutput(path) as output:
                output.save_submission(rows)
                with path.open(newline="", encoding="utf-8") as file:
                    saved_rows = list(csv.DictReader(file))

                self.assertTrue(output.has_submission("abc"))
                self.assertEqual(output.completed_count, 1)
                self.assertEqual(
                    [row["comment_id"] for row in saved_rows], ["one", "two"]
                )

    def test_serialization_failure_does_not_mutate_output(self) -> None:
        class Unserializable:
            def __str__(self) -> str:
                raise ValueError("cannot serialize")

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "output.csv"
            row = make_row()
            row["comment_body"] = Unserializable()

            with ScrapeOutput(path) as output:
                original = path.read_bytes()
                with self.assertRaisesRegex(ScrapeOutputError, str(path)) as raised:
                    output.save_submission([row])

                self.assertEqual(path.read_bytes(), original)
                self.assertIsInstance(raised.exception.__cause__, ValueError)

    def test_invalid_submission_batches_fail_without_mutating_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "output.csv"
            missing_field = make_row()
            del missing_field["comment_score"]
            extra_field = make_row()
            extra_field["unexpected"] = "value"
            invalid_batches = {
                "empty batch": [],
                "empty submission ID": [make_row("")],
                "mixed submission IDs": [make_row("first"), make_row("second")],
                "missing field": [missing_field],
                "extra field": [extra_field],
            }

            with ScrapeOutput(path) as output:
                original = path.read_bytes()
                for label, rows in invalid_batches.items():
                    with self.subTest(label=label):
                        with self.assertRaisesRegex(ScrapeOutputError, str(path)):
                            output.save_submission(rows)
                        self.assertEqual(path.read_bytes(), original)
                        self.assertEqual(output.completed_count, 0)

    def test_duplicate_submission_is_rejected_without_appending_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "output.csv"

            with ScrapeOutput(path) as output:
                output.save_submission([make_row("abc")])
                saved = path.read_bytes()

                with self.assertRaisesRegex(ScrapeOutputError, str(path)):
                    output.save_submission([make_row("abc", "another-comment")])

                self.assertEqual(path.read_bytes(), saved)
                self.assertEqual(output.completed_count, 1)

    def test_open_failure_uses_scrape_output_error_with_path_and_cause(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)

            with self.assertRaisesRegex(ScrapeOutputError, str(path)) as raised:
                with ScrapeOutput(path):
                    pass

            self.assertIsNotNone(raised.exception.__cause__)

    def test_existing_row_without_submission_id_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "output.csv"
            with path.open("w", newline="", encoding="utf-8") as file:
                writer = csv.DictWriter(file, fieldnames=FIELDNAMES)
                writer.writeheader()
                writer.writerow(make_row(""))
            original = path.read_bytes()

            with self.assertRaisesRegex(ScrapeOutputError, str(path)):
                with ScrapeOutput(path):
                    pass

            self.assertEqual(path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
