import unittest
from datetime import datetime, timezone

from backup.plan import dump_name, parse_hhmm, schedule_disabled, seconds_until, select_prunable
from backup.storage import endpoint_url, missing_settings


class Settings(unittest.TestCase):
    BOOKS = {
        "OBJECT_STORE_BUCKET_ENDPOINT": "fsn1.your-objectstorage.com",
        "OBJECT_STORE_ACCESS_KEY_ID": "books-key",
        "OBJECT_STORE_SECRET_ACCESS_KEY": "books-secret",
    }

    def test_bare_host_gets_https(self):
        self.assertEqual(endpoint_url(self.BOOKS), "https://fsn1.your-objectstorage.com")

    def test_only_the_backup_bucket_is_new(self):
        self.assertEqual(missing_settings(self.BOOKS), ["OBJECT_STORE_BACKUP_BUCKET_NAME"])


class Naming(unittest.TestCase):
    def test_name_is_utc_and_sortable(self):
        when = datetime(2026, 11, 16, 3, 30, 5, tzinfo=timezone.utc)
        self.assertEqual(dump_name(when), "vibelib-20261116T033005Z.dump")


class Retention(unittest.TestCase):
    def test_keeps_newest(self):
        keys = [f"vibelib-2026110{d}T033000Z.dump" for d in range(1, 6)]
        self.assertEqual(select_prunable(keys, 3), keys[:2])

    def test_nothing_to_prune(self):
        self.assertEqual(select_prunable(["vibelib-20261101T033000Z.dump"], 3), [])

    def test_ignores_other_objects(self):
        keys = ["notes.txt", "s3-test-x.txt", "vibelib-20261101T033000Z.dump",
                "vibelib-20261102T033000Z.dump"]
        self.assertEqual(select_prunable(keys, 1), ["vibelib-20261101T033000Z.dump"])

    def test_retain_must_be_positive(self):
        with self.assertRaises(ValueError):
            select_prunable([], 0)


class Schedule(unittest.TestCase):
    def test_only_never_disables(self):
        self.assertTrue(schedule_disabled(" Never "))
        self.assertFalse(schedule_disabled(""))

    def test_rejects_bad_times(self):
        for bad in ("3:30pm", "24:00", "", "03"):
            with self.assertRaises(ValueError):
                parse_hhmm(bad)

    def test_later_today_and_tomorrow(self):
        now = datetime(2026, 11, 16, 2, 0)
        self.assertEqual(seconds_until("03:30", now), 5400)
        self.assertEqual(seconds_until("01:00", now), 23 * 3600)



if __name__ == "__main__":
    unittest.main()
