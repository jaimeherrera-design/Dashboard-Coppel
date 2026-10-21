import argparse
import csv
import hashlib
import tempfile
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as arrow_csv
import pyarrow.parquet as pq

from data_cache import source_signature


def table_digest(table: pa.Table) -> bytes:
    sink = pa.BufferOutputStream()
    with pa.ipc.new_stream(sink, table.schema) as writer:
        writer.write_table(table.combine_chunks())
    return hashlib.sha256(sink.getvalue()).digest()


def prepare_parquet(source: Path, target: Path, excluded_date: date) -> dict:
    signature = source_signature(source)
    with source.open(encoding="utf-8-sig", newline="") as handle:
        columns = next(csv.reader(handle, delimiter=";"))
    if "Call end" not in columns or len(columns) != len(set(columns)):
        raise ValueError(f"{source.name}: encabezado invalido.")
    schema = pa.schema([(name, pa.string()) for name in columns])
    total = excluded = kept = 0
    digests = []
    with source.open("rb") as input_handle, arrow_csv.open_csv(
        input_handle,
        read_options=arrow_csv.ReadOptions(block_size=16 * 1024 * 1024),
        parse_options=arrow_csv.ParseOptions(delimiter=";", newlines_in_values=True),
        convert_options=arrow_csv.ConvertOptions(
            column_types={name: pa.string() for name in columns},
            strings_can_be_null=False, null_values=[],
        ),
    ) as reader, pq.ParquetWriter(target, schema, compression="zstd") as writer:
        for batch in reader:
            table = pa.Table.from_batches([batch], schema=schema)
            days = pc.strptime(
                pc.utf8_slice_codeunits(pc.utf8_trim_whitespace(table["Call end"]), 0, 10),
                format="%Y-%m-%d", unit="s", error_is_null=True,
            )
            remove = pc.fill_null(pc.equal(pc.cast(days, pa.date32()), pa.scalar(excluded_date)), False)
            total += table.num_rows
            excluded += pc.sum(pc.cast(remove, pa.int64())).as_py() or 0
            table = table.filter(pc.invert(remove))
            if table.num_rows:
                writer.write_table(table, row_group_size=table.num_rows)
                digests.append(table_digest(table))
                kept += table.num_rows
        print(f"{source.name}: leidos {total:,}, excluidos {excluded:,}, conservados {kept:,}.", flush=True)
    with pq.ParquetFile(target) as parquet:
        if parquet.schema_arrow != schema or parquet.metadata.num_rows != kept or parquet.num_row_groups != len(digests):
            raise ValueError(f"{target.name}: esquema o cantidades no coinciden.")
        for index, expected in enumerate(digests):
            if table_digest(parquet.read_row_group(index)) != expected:
                raise ValueError(f"{target.name}: contenido distinto en grupo {index}.")
    if source_signature(source) != signature:
        raise ValueError(f"{source.name} cambio durante la conversion.")
    return {"source": signature, "read": total, "excluded": excluded, "kept": kept, "bytes": target.stat().st_size}


def convert_originals(root: Path, excluded_date: date) -> list[dict]:
    sources = [root / name for name in ("3meses_v2.csv", "3meses_v3.csv")]
    for source in sources:
        if not source.is_file():
            raise FileNotFoundError(source)
        if source.with_suffix(".parquet").exists():
            raise FileExistsError(source.with_suffix(".parquet"))
    results = []
    published = []
    with tempfile.TemporaryDirectory(prefix=".csv_split_parquet_", dir=root) as folder:
        staging = Path(folder)
        for source in sources:
            results.append(prepare_parquet(source, staging / source.with_suffix(".parquet").name, excluded_date))
        for result in results:
            if source_signature(Path(result["source"][0])) != result["source"]:
                raise ValueError("Una fuente cambio antes de publicar los Parquet.")
        try:
            for source in sources:
                destination = source.with_suffix(".parquet")
                (staging / destination.name).rename(destination)
                published.append(destination)
        except Exception:
            for destination in reversed(published):
                destination.rename(staging / destination.name)
            raise
    print("PARQUET VERIFICADOS: columnas y valores conservados; originales intactos.", flush=True)
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--exclude-date", type=date.fromisoformat, required=True)
    args = parser.parse_args()
    convert_originals(args.root.resolve(), args.exclude_date)
