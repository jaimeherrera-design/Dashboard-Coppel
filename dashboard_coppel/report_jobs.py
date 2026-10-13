from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from threading import RLock
from typing import Callable


ReportKey = tuple[object, ...]
ReportResult = tuple[bytes, bytes]
ProgressCallback = Callable[[float, str], None]
ReportGenerator = Callable[[ProgressCallback], ReportResult]


class ReportJobManager:
    def __init__(self, cache_size: int = 2) -> None:
        if cache_size < 1:
            raise ValueError("El tamano de cache de reportes debe ser positivo.")
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="recurrence-report")
        self._cache_size = cache_size
        self._cache: OrderedDict[ReportKey, ReportResult] = OrderedDict()
        self._jobs: dict[ReportKey, dict] = {}
        self._active_key: ReportKey | None = None
        self._lock = RLock()

    def start(self, key: ReportKey, generate: ReportGenerator) -> bool:
        with self._lock:
            if key in self._cache or self._jobs.get(key, {}).get("state") == "running":
                return True
            if self._active_key is not None:
                return False
            self._active_key = key
            self._jobs[key] = {"state": "running", "progress": 0.0, "message": "Preparando fuentes..."}
            self._executor.submit(self._run, key, generate)
            return True

    def status(self, key: ReportKey) -> dict:
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return {"state": "complete", "progress": 1.0, "message": "PDF listo para descargar.",
                        "result": self._cache[key]}
            if self._active_key is not None and self._active_key != key:
                active_job = self._jobs[self._active_key]
                return {
                    "state": "busy",
                    "progress": active_job["progress"],
                    "message": active_job["message"],
                }
            return dict(self._jobs.get(key, {"state": "idle", "progress": 0.0, "message": ""}))

    def _run(self, key: ReportKey, generate: ReportGenerator) -> None:
        def update_progress(fraction: float, message: str) -> None:
            with self._lock:
                job = self._jobs.get(key)
                if job is not None and job["state"] == "running":
                    job["progress"] = min(max(float(fraction), 0.0), 1.0)
                    job["message"] = message

        try:
            result = generate(update_progress)
        except Exception as exc:
            with self._lock:
                job = self._jobs.get(key, {})
                self._jobs[key] = {
                    "state": "error",
                    "progress": job.get("progress", 0.0),
                    "message": str(exc),
                }
                self._active_key = None
            return

        with self._lock:
            self._cache[key] = result
            self._cache.move_to_end(key)
            while len(self._cache) > self._cache_size:
                expired_key, _ = self._cache.popitem(last=False)
                self._jobs.pop(expired_key, None)
            self._jobs[key] = {"state": "complete", "progress": 1.0, "message": "PDF listo para descargar."}
            self._active_key = None
