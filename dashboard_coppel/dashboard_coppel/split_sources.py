import argparse
import hashlib
import json
import shutil
import stat
import tempfile
from datetime import datetime
from pathlib import Path


MAX_BYTES = 24_000_000


def records(handle):
    buffer = []
    quoted = False
    for line in handle:
        buffer.append(line)
        if line.count(b'"') % 2:
            quoted = not quoted
        if not quoted:
            yield b"".join(buffer)
            buffer = []
    if quoted:
        raise ValueError("CSV con comillas sin cerrar; no se modifican los originales.")
    if buffer:
        yield b"".join(buffer)


def split_file(source: Path, staging: Path, limit: int = MAX_BYTES) -> dict:
    before = source.stat()
    digest = hashlib.sha256()
    paths = []
    count = 0
    output = None
    try:
        with source.open("rb") as handle:
            iterator = records(handle)
            header = next(iterator, None)
            if not header or not header.endswith(b"\n"):
                raise ValueError(f"{source.name}: falta un encabezado terminado en salto de línea.")
            digest.update(header)
            size = limit
            for record in iterator:
                if len(header) + len(record) > limit:
                    raise ValueError(f"{source.name}: un registro supera el límite de {limit} bytes.")
                if size + len(record) > limit:
                    if output is not None:
                        output.close()
                    path = staging / f"{source.stem}_parte_{len(paths) + 1:04d}.csv"
                    output = path.open("xb")
                    paths.append(path)
                    output.write(header)
                    size = len(header)
                output.write(record)
                digest.update(record)
                size += len(record)
                count += 1
            if not paths:
                if len(header) > limit:
                    raise ValueError("El encabezado supera el límite.")
                path = staging / f"{source.stem}_parte_0001.csv"
                path.write_bytes(header)
                paths.append(path)
    finally:
        if output is not None:
            output.close()
    after = source.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError(f"{source.name} cambió durante la división.")
    reconstructed = hashlib.sha256(header)
    for path in paths:
        if path.stat().st_size > limit:
            raise ValueError(f"{path.name} supera el límite.")
        with path.open("rb") as handle:
            if handle.read(len(header)) != header:
                raise ValueError(f"{path.name}: encabezado incorrecto.")
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                reconstructed.update(block)
    if reconstructed.digest() != digest.digest():
        raise ValueError(f"{source.name}: las partes no reconstruyen el original.")
    return {
        "source": source.name, "bytes": before.st_size, "mtime_ns": before.st_mtime_ns,
        "sha256": digest.hexdigest(), "records": count,
        "parts": [{"name": path.name, "bytes": path.stat().st_size} for path in paths],
    }


def split_sources(root: Path, downloads: Path) -> Path:
    sources = sorted(root.glob("*.csv"))
    if not sources:
        raise ValueError("No hay CSV para dividir.")
    if any("_parte_" in source.stem for source in sources):
        raise ValueError("Ya hay partes en la raíz; no se dividen otra vez ni se mezclan versiones.")
    required = sum(source.stat().st_size for source in sources)
    if shutil.disk_usage(root).free < required + 100_000_000:
        raise OSError("No hay espacio suficiente para preparar las partes antes de mover los originales.")
    backup = downloads / f"Coppel_backup_{datetime.now():%Y%m%d_%H%M%S}"
    staging = Path(tempfile.mkdtemp(prefix=".csv_split_", dir=root))
    moved = []
    published = []
    try:
        manifests = []
        for source in sources:
            manifest = split_file(source, staging)
            manifests.append(manifest)
            print(f"{source.name}: {manifest['records']:,} registros, {len(manifest['parts'])} partes verificadas.", flush=True)
        for manifest in manifests:
            source = root / manifest["source"]
            source_info = source.stat()
            if (source_info.st_size, source_info.st_mtime_ns) != (manifest["bytes"], manifest["mtime_ns"]):
                raise ValueError(f"{source.name} cambió antes de moverlo.")
            for part in manifest["parts"]:
                if (root / part["name"]).exists():
                    raise FileExistsError(part["name"])
        backup.mkdir(parents=True, exist_ok=False)
        (backup / "manifest.json").write_text(
            json.dumps({"max_bytes": MAX_BYTES, "sources": manifests}, indent=2), encoding="utf-8",
        )
        try:
            for source in sources:
                shutil.move(str(source), str(backup / source.name))
                moved.append(source.name)
            for manifest in manifests:
                for part in manifest["parts"]:
                    (staging / part["name"]).rename(root / part["name"])
                    published.append(part["name"])
        except Exception:
            for name in reversed(published):
                (root / name).rename(staging / name)
            for name in reversed(moved):
                shutil.move(str(backup / name), str(root / name))
            raise
        print(f"Respaldo: {backup}", flush=True)
        return backup
    finally:
        for path in staging.iterdir():
            path.unlink()
        staging.chmod(stat.S_IREAD | stat.S_IWRITE | stat.S_IEXEC)
        staging.rmdir()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Divide CSV sin cortar registros y mueve originales verificados a Descargas.")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--downloads", type=Path, required=True)
    args = parser.parse_args()
    split_sources(args.root.resolve(), args.downloads.resolve())
