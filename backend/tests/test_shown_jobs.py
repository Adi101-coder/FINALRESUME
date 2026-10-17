"""Shown vs applied job ranking."""

from __future__ import annotations

import unittest

from app.services.shown_jobs import unseen_first


class TestUnseenFirst(unittest.TestCase):
    def test_never_shown_comes_first(self) -> None:
        shown = {"url:https://www.linkedin.com/jobs/view/1/"}
        items = [
            ({"url:https://www.linkedin.com/jobs/view/1/"}, "old"),
            ({"url:https://www.linkedin.com/jobs/view/2/"}, "new"),
        ]
        self.assertEqual(unseen_first(items, shown), ["new", "old"])

    def test_unapplied_shown_jobs_still_returned(self) -> None:
        shown = {"a"}
        items = [({"a"}, "reshow")]
        self.assertEqual(unseen_first(items, shown), ["reshow"])

    def test_empty_shown_keeps_order(self) -> None:
        items = [({"a"}, 1), ({"b"}, 2)]
        self.assertEqual(unseen_first(items, set()), [1, 2])


if __name__ == "__main__":
    unittest.main()
