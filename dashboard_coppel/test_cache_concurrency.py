from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import unittest

import pandas as pd

from data_cache import load_summary, source_signature


class SummaryConcurrencyTests(unittest.TestCase):
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
