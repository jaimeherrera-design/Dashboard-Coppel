import re
from pathlib import Path

import pandas as pd

MORA_FILE = "Mae_Mora.xlsx"
UNMAPPED_MORA = "Sin altura de mora"
_ORDER: dict[str, float] = {}


def _column(frame: pd.DataFrame, name: str) -> str:
    wanted = name.casefold().replace("ñ", "n")
    for column in frame.columns:
        if str(column).strip().casefold().replace("ñ", "n") == wanted:
            return column
    raise ValueError(f"{MORA_FILE} no tiene la columna requerida: {name}")


def read_mora_master(path: str | Path) -> tuple[dict[str, str], dict[str, float]]:
    """Return campaign (casefold) -> altura de mora, and altura de mora -> Posicion."""
    frame = pd.read_excel(path)
    columns = [_column(frame, name) for name in ("Campaña", "ALTURA DE MORA", "Posicion")]
    frame = frame[columns].dropna(how="all").copy()
    frame.columns = ["campaign", "altura", "position"]
    frame["campaign"] = frame["campaign"].fillna("").astype(str).str.strip()
    frame["altura"] = frame["altura"].fillna("").astype(str).str.strip()
    frame["position"] = pd.to_numeric(frame["position"], errors="coerce")
    if frame.empty or frame["campaign"].eq("").any() or frame["altura"].eq("").any():
        raise ValueError(f"{MORA_FILE} está vacío o tiene campañas sin altura de mora.")
    if frame["position"].isna().any():
        raise ValueError(f"{MORA_FILE} tiene alturas de mora sin Posicion numérica.")
    frame["key"] = frame["campaign"].str.casefold()
    if frame.groupby("key")["altura"].nunique().gt(1).any():
        raise ValueError(f"{MORA_FILE} asigna alturas de mora distintas a una misma campaña.")
    mapping = dict(zip(frame["key"], frame["altura"]))
    order = frame.groupby("altura")["position"].min().to_dict()
    return mapping, order


def set_order(order: dict[str, float]) -> None:
    _ORDER.clear()
    _ORDER.update(order)


def position(label) -> tuple[float, str]:
    return _ORDER.get(str(label), float("inf")), str(label)


def sorted_moras(values) -> list:
    return sorted(values, key=position)


def sort_by_mora(table: pd.DataFrame, column: str = "Campaign Name", *, before: list[str] | None = None,
                 after: list[str] | None = None) -> pd.DataFrame:
    if table.empty or column not in table.columns:
        return table
    ranks = table[column].map(lambda value: position(value)[0])
    keys = (before or []) + ["_mora_rank", column] + (after or [])
    return table.assign(_mora_rank=ranks).sort_values(keys, kind="stable").drop(columns="_mora_rank")


def map_campaigns(names: pd.Series, mapping: dict[str, str], excluded_pattern: str) -> pd.Series:
    """Campaigns not in the master keep their name when excluded (test/prueba) so the existing exclusion still applies."""
    lookup = {}
    for name in pd.unique(names):
        text = str(name)
        altura = mapping.get(text.strip().casefold())
        if altura is None:
            altura = text if re.search(excluded_pattern, text, flags=re.IGNORECASE) else UNMAPPED_MORA
        lookup[name] = altura
    return names.map(lookup)
