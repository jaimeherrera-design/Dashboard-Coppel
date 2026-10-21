import argparse
import csv
import hashlib
import json
import shutil
import stat
import tempfile
from datetime import date, datetime
from pathlib import Path

from split_sources import MAX_BYTES, records


def record_date(record: bytes, index: int) -> str:
    fields = next(csv.reader(record.decode("utf-8-sig").splitlines(keepends=True), delimiter=";", strict=True))
    if index >= len(fields):
        raise ValueError("Un registro no contiene la columna Call end.")
    value = fields[index].strip()
    if not value:
        return "sin_fecha"
    try:
        return datetime.fromisoformat(value).date().isoformat()
    except ValueError:
        return "sin_fecha"


def prepare_parts(sources: list[Path], staging: Path, limit: int = MAX_BYTES,
                  *, start: date | None = None, end: date | None = None) -> dict:
    if start is not None and end is not None and start > end:
        raise ValueError("La fecha inicial supera la fecha final.")
    buckets = staging / "days"
    buckets.mkdir()
    handles = {}
    manifests = []
    header = None
    input_count = 0
    input_fingerprint = 0
    try:
        for source in sources:
            before = source.stat()
            count = 0
            excluded = 0
            with source.open("rb") as handle:
                iterator = records(handle)
                current_header = next(iterator, None)
                if not current_header or not current_header.endswith(b"\n"):
                    raise ValueError(f"{source.name}: encabezado inválido.")
                if header is None:
                    header = current_header
                    columns = next(csv.reader(header.decode("utf-8-sig").splitlines(), delimiter=";"))
                    index = columns.index("Call end")
                elif current_header != header:
                    raise ValueError(f"{source.name}: encabezado distinto; no se mezclan esquemas.")
                for record in iterator:
                    day = record_date(record, index)
                    if (start is not None or end is not None) and (
                        day == "sin_fecha"
                        or (start is not None and day < start.isoformat())
                        or (end is not None and day > end.isoformat())
                    ):
                        excluded += 1
                        continue
                    if len(header) + len(record) > limit:
                        raise ValueError(f"{source.name}: un registro supera el límite.")
                    # Preserve record boundaries when combining files with no final newline.
                    if not record.endswith(b"\n"):
                        record += b"\r\n" if header.endswith(b"\r\n") else b"\n"
                    if len(header) + len(record) > limit:
                        raise ValueError(f"{source.name}: un registro supera el límite.")
                    if day not in handles:
                        handles[day] = (buckets / f"{day}.rows").open("xb")
                    handles[day].write(record)
                    input_fingerprint = (input_fingerprint + int.from_bytes(hashlib.sha256(record).digest(), "big")) % (1 << 256)
                    count += 1
                    input_count += 1
            after = source.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ValueError(f"{source.name} cambió durante la preparación.")
            manifests.append({"name": source.name, "bytes": before.st_size, "mtime_ns": before.st_mtime_ns, "records": count, "excluded_records": excluded})
            print(f"Clasificado por fecha: {source.name}, {count:,} registros.", flush=True)
    finally:
        for handle in handles.values():
            handle.close()
    if header is None or input_count == 0:
        raise ValueError("No hay registros para dividir.")
    parts = []
    output = None
    size = limit
    first = last = None
    part_count = 0
    temporary = None

    def finish():
        nonlocal output
        if output is None:
            return
        output.close()
        output = None
        label = "sin_fecha" if first == "sin_fecha" else f"{first}_a_{last}"
        name = f"llamadas_{label}_parte_{len(parts) + 1:04d}.csv"
        temporary.rename(staging / name)
        parts.append({"name": name, "start": first, "end": last, "bytes": size, "records": part_count})

    try:
        for day in sorted(handles, key=lambda value: (value == "sin_fecha", value)):
            if output is not None and day == "sin_fecha":
                finish()
            with (buckets / f"{day}.rows").open("rb") as handle:
                for record in records(handle):
                    if output is None or size + len(record) > limit:
                        finish()
                        temporary = staging / f"part_{len(parts) + 1:04d}.tmp"
                        output = temporary.open("xb")
                        output.write(header)
                        size = len(header)
                        first = day
                        part_count = 0
                    output.write(record)
                    size += len(record)
                    last = day
                    part_count += 1
        finish()
    finally:
        if output is not None:
            output.close()
    output_count = 0
    fingerprint = 0
    for part in parts:
        path = staging / part["name"]
        if path.stat().st_size > limit:
            raise ValueError(f"{path.name}: tamaño excedido.")
        dates = []
        count = 0
        with path.open("rb") as handle:
            iterator = records(handle)
            if next(iterator) != header:
                raise ValueError("Encabezado incorrecto.")
            for record in iterator:
                day = record_date(record, index)
                if (start is not None and day < start.isoformat()) or (end is not None and day > end.isoformat()):
                    raise ValueError(f"{path.name}: registro fuera del intervalo.")
                if not dates or day != dates[-1]:
                    dates.append(day)
                fingerprint = (fingerprint + int.from_bytes(hashlib.sha256(record).digest(), "big")) % (1 << 256)
                output_count += 1
                count += 1
        if dates != sorted(dates) or dates[0] != part["start"] or dates[-1] != part["end"] or count != part["records"]:
            raise ValueError(f"{path.name}: fechas o cantidad no coinciden.")
    if output_count != input_count or fingerprint != input_fingerprint:
        raise ValueError("Los registros de salida no coinciden con las fuentes.")
    return {"max_bytes": limit, "records": input_count, "record_fingerprint": f"{fingerprint:064x}", "sources": manifests, "parts": parts,
            "date_filter": {"start": start.isoformat() if start else None, "end": end.isoformat() if end else None}}


def split_by_dates(root: Path, downloads: Path, *, repository_only: bool = False, output_dir: Path | None = None,
                   start: date | None = None, end: date | None = None, replace: bool = False) -> Path:
    if (start is not None or end is not None) and not repository_only:
        raise ValueError("Filtrar fechas requiere --repository-only para conservar los originales.")
    if replace and (output_dir is None or not repository_only):
        raise ValueError("Reemplazar requiere destino personalizado y --repository-only.")
    sources = sorted(path for path in root.iterdir() if path.is_file() and path.suffix.casefold() == ".csv")
    if not sources:
        raise ValueError("No hay CSV en la raíz.")
    if shutil.disk_usage(root).free < sum(p.stat().st_size for p in sources) * 2 + 200_000_000:
        raise OSError("No hay espacio suficiente para preparar y verificar las partes.")
    previous = []
    if output_dir is not None:
        if not repository_only:
            raise ValueError("El destino personalizado requiere --repository-only para conservar los originales.")
        output_dir.mkdir(parents=True, exist_ok=True)
        if any(output_dir.glob("*.csv")) or (output_dir / "manifest.json").exists():
            if not replace:
                raise FileExistsError("El destino ya contiene CSV o un manifiesto; no se sobrescribe.")
            old = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
            names = [part["name"] for part in old["parts"]]
            if any(Path(name).name != name or not name.startswith("llamadas_") or not name.endswith(".csv") for name in names):
                raise ValueError("El manifiesto previo contiene rutas no validas.")
            if set(names) != {path.name for path in output_dir.glob("*.csv")}:
                raise ValueError("Los CSV del destino no coinciden con su manifiesto.")
            previous = [output_dir / name for name in names] + [output_dir / "manifest.json"]
    staging = Path(tempfile.mkdtemp(prefix=".csv_split_dates_", dir=output_dir.parent if output_dir is not None else root))
    backup = downloads / f"Coppel_backup_fechas_{datetime.now():%Y%m%d_%H%M%S}"
    moved = []
    published = []
    retired = []
    try:
        manifest = prepare_parts(sources, staging, start=start, end=end)
        for info in manifest["sources"]:
            current = (root / info["name"]).stat()
            if (current.st_size, current.st_mtime_ns) != (info["bytes"], info["mtime_ns"]):
                raise ValueError("Una fuente cambió antes de publicar.")
        if repository_only:
            if output_dir is not None:
                (staging / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
                old_dir = staging / "previous"
                old_dir.mkdir()
                try:
                    for path in previous:
                        path.rename(old_dir / path.name)
                        retired.append(path.name)
                    for name in [part["name"] for part in manifest["parts"]] + ["manifest.json"]:
                        (staging / name).rename(output_dir / name)
                        published.append(name)
                except Exception:
                    for name in reversed(published):
                        (output_dir / name).rename(staging / name)
                    for name in reversed(retired):
                        (old_dir / name).rename(output_dir / name)
                    raise
                print(f"DESTINO VERIFICADO: {manifest['records']:,} registros, {len(manifest['parts'])} CSV en {output_dir}. Originales intactos.", flush=True)
                return output_dir
            destination = root / "csv_repositorio"
            if destination.exists():
                raise FileExistsError(f"Ya existe {destination}; no se sobrescribe.")
            (staging / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            buckets = staging / "days"
            for child in buckets.iterdir():
                child.unlink()
            buckets.chmod(stat.S_IREAD | stat.S_IWRITE | stat.S_IEXEC)
            buckets.rmdir()
            staging.rename(destination)
            print(f"REPOSITORIO VERIFICADO: {manifest['records']:,} registros, {len(manifest['parts'])} archivos en {destination}. Originales intactos.", flush=True)
            return destination
        for part in manifest["parts"]:
            if (root / part["name"]).exists():
                raise FileExistsError(part["name"])
        backup.mkdir(parents=True, exist_ok=False)
        (backup / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        try:
            for source in sources:
                shutil.move(str(source), str(backup / source.name))
                moved.append(source.name)
            for part in manifest["parts"]:
                (staging / part["name"]).rename(root / part["name"])
                published.append(part["name"])
        except Exception:
            for name in reversed(published):
                (root / name).rename(staging / name)
            for name in reversed(moved):
                shutil.move(str(backup / name), str(root / name))
            raise
        print(f"VERIFICADO: {manifest['records']:,} registros, {len(published)} archivos. Respaldo: {backup}", flush=True)
        return backup
    finally:
        for path in staging.iterdir() if staging.exists() else []:
            if path.is_dir():
                for child in path.iterdir():
                    child.unlink()
                path.chmod(stat.S_IREAD | stat.S_IWRITE | stat.S_IEXEC)
                path.rmdir()
            else:
                path.unlink()
        if staging.exists():
            staging.chmod(stat.S_IREAD | stat.S_IWRITE | stat.S_IEXEC)
            staging.rmdir()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--downloads", type=Path, required=True)
    parser.add_argument("--repository-only", action="store_true")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--start", type=date.fromisoformat)
    parser.add_argument("--end", type=date.fromisoformat)
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    split_by_dates(
        args.root.resolve(), args.downloads.resolve(), repository_only=args.repository_only,
        output_dir=args.output_dir.resolve() if args.output_dir is not None else None,
        start=args.start, end=args.end, replace=args.replace,
    )
