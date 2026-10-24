"""Offline checks for release-aware ECB download caching."""

from datetime import datetime
import io
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
import zipfile

import pandas as pd

from rates import download_ecb, latest_expected_ecb_release, ECB_HIST_ZIP, ECB_DAILY_ZIP


def archive(day, daily=False):
    date = pd.Timestamp(day).strftime("%d %B %Y") if daily else day
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as zipped:
        zipped.writestr("eurofxref.csv", f"Date,USD\n{date},1.12\n")
    return stream.getvalue()


class ECBCacheTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        previous = Path.cwd()
        os.chdir(directory.name)
        self.addCleanup(os.chdir, previous)
        self.cache = Path("cache")
        self.log = patch("rates.log").start()
        self.addCleanup(patch.stopall)

    def download(self, stamp="2026-10-24T12:00:00+02:00", **kwargs):
        return download_ecb(
            Mock(), None, None, cache_dir=self.cache,
            now=datetime.fromisoformat(stamp), **kwargs,
        )

    def populate(self, day="2026-10-23"):
        self.cache.mkdir(exist_ok=True)
        (self.cache / "eurofxref-hist.zip").write_bytes(archive(day))
        (self.cache / "eurofxref.zip").write_bytes(archive(day, daily=True))

    def test_release_calendar_handles_cutoff_dst_weekends_and_target_holidays(self):
        for stamp, expected in (
            ("2026-10-23T13:59:00+00:00", "2026-10-22"),
            ("2026-10-23T14:00:00+00:00", "2026-10-23"),
            ("2026-10-24T12:00:00+02:00", "2026-10-23"),
            ("2026-10-26T14:59:00+00:00", "2026-10-23"),
            ("2026-10-26T15:00:00+00:00", "2026-10-26"),
            ("2026-04-03T17:00:00+02:00", "2026-04-02"),
            ("2026-04-06T17:00:00+02:00", "2026-04-02"),
            ("2026-05-04T15:00:00+02:00", "2026-04-30"),
            ("2026-12-28T15:00:00+01:00", "2026-12-24"),
            ("2027-01-01T17:00:00+01:00", "2026-12-31"),
        ):
            with self.subTest(stamp=stamp):
                self.assertEqual(latest_expected_ecb_release(datetime.fromisoformat(stamp)), pd.Timestamp(expected))

    def test_first_download_is_saved_and_second_run_skips_network(self):
        replies = [Mock(content=archive("2026-10-23")), Mock(content=archive("2026-10-23", True))]
        with patch("rates.request_with_retries", side_effect=replies) as request:
            first = self.download()
            second = self.download()
        self.assertEqual(request.call_count, 2)
        pd.testing.assert_frame_equal(first, second)
        self.assertTrue((self.cache / "eurofxref-hist.zip").exists())
        self.assertTrue(any("--force" in call.args[0] for call in self.log.call_args_list))

    def test_refresh_only_after_new_release_and_force_bypasses_freshness(self):
        self.populate()
        with patch("rates.request_with_retries") as request:
            self.download("2026-10-26T15:59:00+01:00")
            request.assert_not_called()
        for stamp, force, day in (
            ("2026-10-24T12:00:00+02:00", True, "2026-10-23"),
            ("2026-10-26T16:00:00+01:00", False, "2026-10-26"),
        ):
            with patch("rates.request_with_retries", side_effect=[
                Mock(content=archive(day)), Mock(content=archive(day, True)),
            ]) as request:
                self.download(stamp, force=force)
                self.assertEqual(request.call_count, 2)

    def test_missing_or_corrupt_files_are_refreshed_independently(self):
        self.populate()
        (self.cache / "eurofxref-hist.zip").write_bytes(b"broken")
        with patch("rates.request_with_retries", return_value=Mock(content=archive("2026-10-23"))) as request:
            self.download()
        self.assertEqual(request.call_count, 1)
        self.assertEqual(request.call_args.args[1], ECB_HIST_ZIP)
        (self.cache / "eurofxref.zip").unlink()
        with patch("rates.request_with_retries", return_value=Mock(content=archive("2026-10-23", True))) as request:
            self.download()
        self.assertEqual(request.call_args.args[1], ECB_DAILY_ZIP)

    def test_stale_response_is_retried_and_invalid_response_preserves_cache(self):
        self.populate()
        replies = [Mock(content=archive("2026-10-23")), Mock(content=archive("2026-10-23", True))] * 2
        with patch("rates.request_with_retries", side_effect=replies) as request:
            self.download("2026-10-26T17:00:00+01:00")
            self.download("2026-10-26T17:00:00+01:00")
        self.assertEqual(request.call_count, 4)
        previous = (self.cache / "eurofxref-hist.zip").read_bytes()
        with patch("rates.request_with_retries", return_value=Mock(content=b"broken")):
            with self.assertRaises(zipfile.BadZipFile):
                self.download(force=True)
        self.assertEqual((self.cache / "eurofxref-hist.zip").read_bytes(), previous)

    def test_existing_working_directory_downloads_are_reused(self):
        Path("eurofxref-hist.zip").write_bytes(archive("2026-10-23"))
        Path("eurofxref.csv").write_text("Date,USD\n23 October 2026,1.12\n")
        with patch("rates.request_with_retries") as request:
            self.download()
        request.assert_not_called()

    def test_explicit_csv_overrides_stay_local_even_with_force(self):
        Path("hist.csv").write_text("Date,USD\n2026-10-22,1.12\n")
        Path("daily.csv").write_text("Date,USD\n23 October 2026,1.13\n")
        with patch("rates.request_with_retries") as request:
            result = download_ecb(Mock(), Path("daily.csv"), Path("hist.csv"), force=True)
        request.assert_not_called()
        self.assertEqual(result.loc[pd.Timestamp("2026-10-23"), "USD"], 1.13)


if __name__ == "__main__":
    unittest.main()
