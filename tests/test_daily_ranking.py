import sqlite3
import unittest
from datetime import datetime, timezone

from daily_ranking import (
    claim_daily_publication,
    daily_publication_context,
    release_daily_publication,
)


class DailyRankingTests(unittest.TestCase):
    def test_paris_midnight_in_winter(self):
        self.assertEqual(
            daily_publication_context(datetime(2026, 10, 31, 23, 0, tzinfo=timezone.utc)),
            ("2026-10-31", "2026-10"),
        )
        self.assertIsNone(
            daily_publication_context(datetime(2026, 10, 31, 22, 59, tzinfo=timezone.utc))
        )

    def test_paris_midnight_in_summer_and_month_change(self):
        self.assertEqual(
            daily_publication_context(datetime(2026, 6, 30, 22, 0, tzinfo=timezone.utc)),
            ("2026-06-30", "2026-06"),
        )

    def test_claim_is_atomic_and_failure_can_be_retried(self):
        with sqlite3.connect(":memory:") as connection:
            connection.execute("CREATE TABLE announced_days (guild_id INTEGER, day_key TEXT, announced_at TEXT, PRIMARY KEY(guild_id, day_key))")
            self.assertTrue(claim_daily_publication(connection, 123, "2026-10-28"))
            self.assertFalse(claim_daily_publication(connection, 123, "2026-10-28"))
            self.assertTrue(claim_daily_publication(connection, 456, "2026-10-28"))
            release_daily_publication(connection, 123, "2026-10-28")
            self.assertTrue(claim_daily_publication(connection, 123, "2026-10-28"))


if __name__ == "__main__":
    unittest.main()
