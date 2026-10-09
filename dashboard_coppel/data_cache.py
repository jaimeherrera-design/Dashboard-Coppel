import hashlib
import json
import sqlite3
import tempfile
from collections.abc import Callable, Iterable, Iterator
from contextlib import closing
from pathlib import Path
from threading import Lock, RLock

import pandas as pd


SUMMARY_VERSION = "hourly-outcome-v1"
SUMMARY_KEYS = ["Call end", "Call Type", "Campaign Name", "Term Reason", "Call Outcome name"]
SUMMARY_METRICS = ["calls", "talk_sum", "wrap_sum", "wait_sum"]
FileSignature = tuple[str, int, int]
_SUMMARY_LOCKS: dict[str, RLock] = {}
_LOCKS_GUARD = Lock()


def source_signature(path: Path) -> FileSignature:
    info = path.stat()
    return str(path.resolve()), info.st_mtime_ns, info.st_size


def load_summary(
    signature: FileSignature,
    chunks: Callable[[], Iterable[pd.DataFrame]],
    status=None,
    *,
    keys: list[str] | None = None,
    version: str = SUMMARY_VERSION,
    namespace: str = "",
) -> pd.DataFrame:
    lock_key = signature[0] + namespace
    with _LOCKS_GUARD:
        lock = _SUMMARY_LOCKS.setdefault(lock_key, RLock())
    with lock:
        return _load_summary(signature, chunks, status, keys=keys, version=version, namespace=namespace)


def _load_summary(
    signature: FileSignature,
    chunks: Callable[[], Iterable[pd.DataFrame]],
    status=None,
    *,
    keys: list[str] | None = None,
    version: str = SUMMARY_VERSION,
    namespace: str = "",
) -> pd.DataFrame:
    cache = _prepare_summary(signature, chunks, status, keys=keys, version=version, namespace=namespace)
    with closing(sqlite3.connect(cache)) as connection:
        result = pd.read_sql_query("SELECT * FROM summary", connection)
    if source_signature(Path(signature[0])) != signature:
        raise ValueError(f"La fuente cambio durante la carga: {Path(signature[0]).name}. Recarga el dashboard.")
    return result


def iter_summary(
    signature: FileSignature,
    chunks: Callable[[], Iterable[pd.DataFrame]],
    status=None,
    *,
    keys: list[str],
    version: str,
    namespace: str,
    batch_size: int = 25_000,
) -> Iterator[pd.DataFrame]:
    if batch_size < 1:
        raise ValueError("El tamano del lote del resumen debe ser positivo.")
    lock_key = signature[0] + namespace
    with _LOCKS_GUARD:
        lock = _SUMMARY_LOCKS.setdefault(lock_key, RLock())
    with lock:
        cache = _prepare_summary(
            signature, chunks, status, keys=keys, version=version,
            namespace=namespace, date_format="%Y-%m-%d",
        )
        with closing(sqlite3.connect(cache)) as connection:
            for batch in pd.read_sql_query("SELECT * FROM summary", connection, chunksize=batch_size):
                if source_signature(Path(signature[0])) != signature:
                    raise ValueError(f"La fuente cambio durante la carga: {Path(signature[0]).name}. Recarga el dashboard.")
                yield batch
        if source_signature(Path(signature[0])) != signature:
            raise ValueError(f"La fuente cambio durante la carga: {Path(signature[0]).name}. Recarga el dashboard.")


def _prepare_summary(
    signature: FileSignature,
    chunks: Callable[[], Iterable[pd.DataFrame]],
    status=None,
    *,
    keys: list[str] | None = None,
    version: str = SUMMARY_VERSION,
    namespace: str = "",
    date_format: str = "%Y-%m-%d %H:00:00",
) -> Path:
    keys = SUMMARY_KEYS if keys is None else keys
    source = Path(signature[0])
    if source_signature(source) != signature:
        raise ValueError(f"La fuente cambio durante la carga: {source.name}. Recarga el dashboard.")
    directory = source.parent / ".dashboard_cache"
    directory.mkdir(exist_ok=True)
    key = hashlib.sha256((str(source.resolve()) + namespace).encode("utf-8")).hexdigest()
    cache = directory / f"{key}.sqlite"
    identity = json.dumps([version, *signature])
    if cache.exists():
        with closing(sqlite3.connect(cache)) as connection:
            stored = connection.execute("SELECT identity FROM metadata").fetchone()
            if stored is not None and stored[0] == identity:
                if status is not None:
                    status.update(label=f"Usando resumen guardado: {source.name}", state="running")
                if source_signature(source) != signature:
                    raise ValueError(f"La fuente cambio durante la carga: {source.name}. Recarga el dashboard.")
                return cache

    columns = keys + SUMMARY_METRICS
    quoted_keys = ", ".join(f'"{name}"' for name in keys)
    definitions = [f'"{name}" TEXT NOT NULL' for name in keys]
    definitions += ['"calls" INTEGER NOT NULL']
    definitions += [f'"{name}" REAL NOT NULL' for name in SUMMARY_METRICS[1:]]
    insert_columns = ", ".join(f'"{name}"' for name in columns)
    updates = ", ".join(f'"{name}" = "{name}" + excluded."{name}"' for name in SUMMARY_METRICS)
    statement = (
        f"INSERT INTO summary ({insert_columns}) VALUES ({', '.join('?' for _ in columns)}) "
        f"ON CONFLICT ({quoted_keys}) DO UPDATE SET {updates}"
    )
    with tempfile.NamedTemporaryFile(dir=directory, suffix=".sqlite", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        with closing(sqlite3.connect(temporary)) as connection:
            connection.execute(
                f"CREATE TABLE summary ({', '.join(definitions)}, PRIMARY KEY ({quoted_keys})) WITHOUT ROWID"
            )
            connection.execute("CREATE TABLE metadata (identity TEXT NOT NULL)")
            for chunk in chunks():
                chunk = chunk.copy()
                chunk["Call end"] = chunk["Call end"].dt.strftime(date_format).fillna("")
                grouped = chunk.groupby(keys, dropna=False).agg(
                    calls=("Talk Time", "size"),
                    talk_sum=("Talk Time", "sum"),
                    wrap_sum=("Wrap up time", "sum"),
                    wait_sum=("Wait Time", "sum"),
                ).reset_index()
                connection.executemany(statement, grouped[columns].itertuples(index=False, name=None))
            if source_signature(source) != signature:
                raise ValueError(f"La fuente cambio durante la carga: {source.name}. Recarga el dashboard.")
            connection.execute("INSERT INTO metadata VALUES (?)", (identity,))
            connection.commit()
        temporary.replace(cache)
        return cache
    finally:
        temporary.unlink(missing_ok=True)
