from pathlib import Path
import re

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from data_cache import FileSignature, source_signature
from interactive_table import render_interactive_table
from report_view import BLUE, LIGHT_BLUE, GREEN, presentation_metrics, report_chart


DETAIL_VERSION = "did-phone-prefix-v1"
METRICS = ["calls", "contacts", "talk_sum", "wrap_sum", "wait_sum"]
UNMAPPED = "Sin cruce"


def numeric_code(value: str) -> str:
    value = str(value).strip()
    if re.fullmatch(r"\d+\.0+", value):
        value = value.split(".")[0]
    if not value.isdigit():
        return ""
    return value.lstrip("0") or "0"


def phone_prefix(value: str) -> str:
    value = re.sub(r"[\s()+-]", "", str(value).strip())
    if re.fullmatch(r"\d+\.0+", value):
        value = value.split(".")[0]
    if len(value) == 12 and value.startswith("52"):
        value = value[2:]
    elif len(value) == 13 and value.startswith("521"):
        value = value[3:]
    return value[:3] if len(value) == 10 and value.isdigit() else ""


@st.cache_data(show_spinner=False)
def load_detail_masters(did_signature: FileSignature, lada_signature: FileSignature) -> tuple[pd.DataFrame, dict]:
    did = pd.read_excel(did_signature[0], dtype=str).dropna(how="all")
    lada = pd.read_excel(lada_signature[0], dtype=str).dropna(how="all")
    did.columns = did.columns.str.strip()
    lada.columns = lada.columns.str.strip()
    for table, required, name in [
        (did, ["DDI", "NOMBRE", "HOMOLOGACION"], "Mae_did.xlsx"),
        (lada, ["NIR", "ESTADO"], "Mae_lada.xlsx"),
    ]:
        missing = set(required).difference(table.columns)
        if missing:
            raise ValueError(f"{name}: faltan columnas {', '.join(sorted(missing))}.")
        if table.empty or table[required].isna().any().any():
            raise ValueError(f"{name}: maestro vacío o con campos obligatorios sin dato.")
        for column in required:
            table[column] = table[column].str.strip()
            if table[column].eq("").any():
                raise ValueError(f"{name}: {column} contiene valores vacíos.")
    did["DDI"] = did["DDI"].map(numeric_code)
    lada["NIR"] = lada["NIR"].map(numeric_code)
    if did["DDI"].eq("").any() or not lada["NIR"].str.fullmatch(r"\d{2,3}").all():
        raise ValueError("Los DDI deben ser numéricos y las ladas deben tener dos o tres dígitos.")
    for table, key, values, name in [
        (did, "DDI", ["NOMBRE", "HOMOLOGACION"], "Mae_did.xlsx"),
        (lada, "NIR", ["ESTADO"], "Mae_lada.xlsx"),
    ]:
        if table.groupby(key)[values].nunique().gt(1).any().any():
            raise ValueError(f"{name}: hay cruces contradictorios para el mismo {key}.")
    for signature in [did_signature, lada_signature]:
        if source_signature(Path(signature[0])) != signature:
            raise ValueError("Un maestro de detalle cambió durante la lectura. Recarga el dashboard.")
    return did.drop_duplicates("DDI").set_index("DDI"), lada.drop_duplicates("NIR").set_index("NIR")["ESTADO"].to_dict()


def classify_detail(chunk: pd.DataFrame, did: pd.DataFrame, ladas: dict) -> pd.DataFrame:
    chunk = chunk.copy()
    chunk["did"] = chunk["DDI"].map(did["HOMOLOGACION"]).fillna(UNMAPPED)
    chunk["provider"] = chunk["DDI"].map(did["NOMBRE"]).fillna(UNMAPPED)
    prefix = chunk["phone_prefix"]
    chunk["state"] = prefix.map(ladas).fillna(prefix.str[:2].map(ladas)).fillna(UNMAPPED)
    chunk["month"] = chunk["Call end"].dt.to_period("M").astype(str)
    return chunk


def combine_groups(parts: list[pd.DataFrame], dimensions: list[str]) -> pd.DataFrame:
    if not parts:
        return pd.DataFrame(columns=dimensions + METRICS)
    return pd.concat(parts).groupby(dimensions, as_index=False)[METRICS].sum()


def hierarchy_rows(table: pd.DataFrame, dimensions: list[str]) -> pd.DataFrame:
    rows = []

    def visit(frame, level, parent):
        grouped = frame.groupby(dimensions[level], as_index=False)[METRICS].sum().sort_values("calls", ascending=False)
        for _, row in grouped.iterrows():
            identifier = str(len(rows))
            label = str(row[dimensions[level]])
            rows.append({
                "label": label, "_id": identifier, "_parent": parent, "_depth": level,
                **{metric: row[metric] for metric in METRICS},
            })
            if level + 1 < len(dimensions):
                visit(frame.loc[frame[dimensions[level]].eq(label)], level + 1, identifier)

    if not table.empty:
        visit(table, 0, "")
    return presentation_metrics(pd.DataFrame(rows, columns=["label", "_id", "_parent", "_depth"] + METRICS))


def detail_table(table: pd.DataFrame, dimensions: list[str], title: str, height: int = 380, performance: bool = True, *, page_size: int = 0) -> None:
    display = hierarchy_rows(table, dimensions)
    display["rate"] *= 100
    columns = [("label", title), ("calls", "Total llamadas"), ("contacts", "Llamadas contactadas"), ("rate", "% Contactabilidad")]
    if performance:
        columns += [("tmo", "TMO (s)"), ("idle", "Idle (s)")]
    totals = presentation_metrics(pd.DataFrame([table[METRICS].sum()])).iloc[0].to_dict()
    totals["label"] = "Total"
    totals["rate"] *= 100
    render_interactive_table(display, columns, title, height=height, hierarchy=True, totals=totals, page_size=page_size)


def did_volume_figure(table: pd.DataFrame) -> go.Figure:
    table = presentation_metrics(table).sort_values("calls", ascending=False)
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(go.Bar(
        x=table["did"], y=table["calls"], name="Total llamadas", marker_color=BLUE,
        text=table["calls"], texttemplate="%{y:,.0f}", textposition="outside",
        cliponaxis=False,
    ), secondary_y=False)
    fig.add_trace(go.Scatter(
        x=table["did"], y=table["rate"], mode="lines+markers+text",
        text=[f"{value:.2%}" for value in table["rate"]], textposition="top center",
        name="% Contactabilidad", line=dict(color=GREEN),
    ), secondary_y=True)
    fig.update_layout(title="Total llamadas y % contactabilidad por DID")
    fig.update_yaxes(title_text="Llamadas", secondary_y=False, rangemode="tozero")
    fig.update_yaxes(title_text="Contactabilidad", tickformat=".0%", range=[0, 1.15], secondary_y=True, showgrid=False)
    return fig


def state_figure(table: pd.DataFrame, total: dict) -> go.Figure:
    table = presentation_metrics(table)
    fig = go.Figure(go.Scatter(
        x=table["tmo"], y=table["rate"], mode="markers",
        customdata=list(zip(table["state"], table["calls"], table["contacts"])),
        marker=dict(color=LIGHT_BLUE, size=table["calls"], sizemode="area",
                    sizeref=max(float(table["calls"].max()), 1) / 400, sizemin=5),
        hovertemplate="%{customdata[0]}<br>Llamadas: %{customdata[1]:,.0f}<br>Contactos: %{customdata[2]:,.0f}<br>TMO: %{x:.2f} s<br>Contactabilidad: %{y:.2%}<extra></extra>",
    ))
    calls = max(total["total"], 1)
    tmo = (total["talk_sum"] + total["wrap_sum"]) / calls
    rate = total["contacts"] / calls
    fig.add_vline(x=tmo, line_dash="dot", line_color=GREEN)
    fig.add_hline(y=rate, line_dash="dot", line_color=GREEN)
    fig.update_layout(title="TMO y % contactabilidad por estado", showlegend=False)
    fig.update_xaxes(title="TMO (segundos)")
    fig.update_yaxes(title="Contactabilidad", tickformat=".1%")
    return fig


def render_detail(result: dict, totals: dict) -> None:
    if result["did"].empty:
        st.info("No hay llamadas para los filtros seleccionados.")
        return
    section = st.selectbox(
        "Componente del detalle",
        ["DID / Mes / Proveedor", "DID / Campaña / Mes",
         "Volumen por DID", "Estado / Mes / Proveedor", "TMO / Contactabilidad por estado"],
        key="detail-render-section",
    )
    if section == "DID / Mes / Proveedor":
        detail_table(result["did_month"], ["did", "month", "provider"], section, height=700, page_size=100)
    elif section == "DID / Campaña / Mes":
        detail_table(result["did_campaign"], ["did", "Campaign Name", "month"], section, height=700, performance=False, page_size=100)
    elif section == "Volumen por DID":
        report_chart(did_volume_figure(result["did"]), 380)
    elif section == "Estado / Mes / Proveedor":
        detail_table(result["state_month"], ["state", "month", "provider"], section, height=700, page_size=100)
    else:
        report_chart(state_figure(result["state"], totals), 500)
