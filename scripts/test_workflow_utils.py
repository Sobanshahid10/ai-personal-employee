"""Offline tests for shared Day 8 workflow primitives."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from workflow_utils import (  # noqa: E402
    WorkflowFileError,
    move_file,
    parse_frontmatter,
    write_frontmatter,
)


class WorkflowUtilsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_frontmatter_round_trip_from_path(self) -> None:
        path = self.root / "item.md"
        metadata = {"action_id": "email_123", "steps": ["Review", "Approve"]}

        write_frontmatter(path, metadata, "Exact body\nsecond line")
        parsed, body = parse_frontmatter(path)

        self.assertEqual(parsed, metadata)
        self.assertEqual(body, "Exact body\nsecond line")

    def test_move_file_moves_into_directory(self) -> None:
        source = self.root / "source.md"
        destination_dir = self.root / "Done"
        source.write_text("content", encoding="utf-8")
        destination_dir.mkdir()

        result = move_file(source, destination_dir)

        self.assertEqual(result, destination_dir / "source.md")
        self.assertFalse(source.exists())
        self.assertEqual(result.read_text(encoding="utf-8"), "content")

    def test_move_file_refuses_overwrite(self) -> None:
        source = self.root / "source.md"
        destination = self.root / "destination.md"
        source.write_text("new", encoding="utf-8")
        destination.write_text("existing", encoding="utf-8")

        with self.assertRaises(WorkflowFileError):
            move_file(source, destination)

        self.assertEqual(destination.read_text(encoding="utf-8"), "existing")


if __name__ == "__main__":
    unittest.main()
