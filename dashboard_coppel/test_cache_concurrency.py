from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import unittest

import pandas as pd

from data_cache import load_summary, iter_summary, source_signature, SUMMARY_KEYS


class SummaryConcurrencyTests(unittest.TestCase):
    def test_concurrent_daily_readers_build_once_and_keep_hourly_cache_separate(self):
        with TemporaryDirectory() as folder:
            source = Path(folder) / "calls.csv"
            source.write_text("source")
            signature = source_signature(source)
            started, release = Event(), Event()
            reads = []
            def chunks():
                reads.append(1)
                started.set()
                if not release.wait(10):
                    raise TimeoutError("Test did not release daily builder")
                yield pd.DataFrame({
                    "Call end": pd.to_datetime(["2026-06-01 10:00:00", "2026-06-01 11:00:00"]),
                    "Call Type": ["dialer"] * 2, "Campaign Name": ["Campaign"] * 2,
                    "Term Reason": ["DIALER"] * 2, "Call Outcome name": ["No Answer"] * 2,
                    "Talk Time": [0, 0], "Wrap up time": [0, 0], "Wait Time": [2, 3],
                })
            def daily():
                return pd.concat(list(iter_summary(signature, chunks, keys=SUMMARY_KEYS,
                                                   version="daily-v2", namespace="detail-v2", batch_size=1)))
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(daily)
                self.assertTrue(started.wait(10))
                second = pool.submit(daily)
                release.set()
                one, two = first.result(10), second.result(10)
            pd.testing.assert_frame_equal(one, two)
            self.assertEqual(len(reads), 1)
            self.assertEqual(len(one), 1)
            self.assertEqual(one["calls"].sum(), 2)
            hourly = load_summary(signature, chunks)
            self.assertEqual(len(hourly), 2)
            self.assertEqual(hourly["wait_sum"].sum(), one["wait_sum"].sum())
            self.assertEqual(len(list((Path(folder) / ".dashboard_cache").glob("*.sqlite"))), 2)

    def test_concurrent_requests_build_source_once(self):
        with TemporaryDirectory() as folder:
            source = Path(folder) / "calls.csv"
            source.write_text("source")
            signature = source_signature(source)
            started = Event()
            release = Event()
            reads = []

            def chunks():
                reads.append(1)
                started.set()
                if not release.wait(10):
                    raise TimeoutError("Test did not release builder")
                yield pd.DataFrame({
                    "Call end": pd.to_datetime(["2026-06-01 10:00:00"]),
                    "Call Type": ["dialer"], "Campaign Name": ["Campaign"],
                    "Term Reason": ["DIALER"], "Call Outcome name": ["No Answer"],
                    "Talk Time": [0], "Wrap up time": [0], "Wait Time": [2],
                })

            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(load_summary, signature, chunks)
                self.assertTrue(started.wait(10))
                second = pool.submit(load_summary, signature, chunks)
                release.set()
                pd.testing.assert_frame_equal(first.result(10), second.result(10))
            self.assertEqual(len(reads), 1)


if __name__ == "__main__":
    unittest.main()
