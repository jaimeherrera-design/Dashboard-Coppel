from threading import Event
from time import monotonic, sleep
import unittest

from report_jobs import ReportJobManager


class ReportJobManagerTests(unittest.TestCase):
    def test_report_runs_in_background_reuses_result_and_reports_progress(self):
        manager = ReportJobManager()
        started, release, finished = Event(), Event(), Event()
        result = (b"%PDF-test", b"csv")
        calls = []

        def generate(progress):
            calls.append(1)
            progress(0.4, "Leyendo fuentes...")
            started.set()
            if not release.wait(10):
                raise TimeoutError("Test did not release report")
            finished.set()
            return result

        key = ("source", "period")
        self.assertTrue(manager.start(key, generate))
        self.assertTrue(started.wait(10))
        self.assertTrue(manager.start(key, generate))
        self.assertFalse(manager.start(("other", "period"), generate))
        self.assertEqual(manager.status(key)["progress"], 0.4)
        self.assertEqual(manager.status(("other", "period"))["state"], "busy")

        release.set()
        self.assertTrue(finished.wait(10))
        deadline = monotonic() + 10
        while monotonic() < deadline:
            status = manager.status(key)
            if status["state"] == "complete":
                break
            sleep(0.01)
        self.assertEqual(status["state"], "complete")
        self.assertEqual(status["result"], result)
        self.assertTrue(manager.start(key, generate))
        self.assertEqual(calls, [1])

    def test_report_errors_are_available_for_display_and_can_be_retried(self):
        manager = ReportJobManager()
        failed = Event()

        def generate(_progress):
            try:
                raise ValueError("Fuente de prueba invalida")
            finally:
                failed.set()

        key = ("source", "period")
        self.assertTrue(manager.start(key, generate))
        self.assertTrue(failed.wait(10))
        deadline = monotonic() + 10
        while monotonic() < deadline:
            status = manager.status(key)
            if status["state"] == "error":
                break
            sleep(0.01)
        self.assertEqual(status["state"], "error")
        self.assertEqual(status["message"], "Fuente de prueba invalida")
        self.assertTrue(manager.start(key, lambda _progress: (b"%PDF-test", b"csv")))


if __name__ == "__main__":
    unittest.main()
