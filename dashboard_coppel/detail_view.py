from pathlib import Path
from html import escape
import re

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from data_cache import FileSignature, source_signature
from interactive_table import render_interactive_table
from report_view import BLUE, LIGHT_BLUE, GREEN, presentation_metrics, report_chart


DETAIL_VERSION = "daily-did-phone-prefix-v2"
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
    if page_size:
        render_detail_page(display, columns, title, totals, height, page_size, len(dimensions))
    else:
        render_interactive_table(display, columns, title, height=height, hierarchy=True, totals=totals)


def select_detail_rows(display: pd.DataFrame, query: str, depth: int, sort_key: str, ascending: bool) -> pd.DataFrame:
    query = query.strip().casefold()
    by_id = display.set_index("_id")
    included = set()
    if query:
        searchable = ["label", "calls", "contacts", "rate", "tmo", "idle"]
        matches = pd.Series(False, index=display.index)
        for column in searchable:
            matches |= display[column].astype(str).str.casefold().str.contains(query, regex=False)
        for identifier in display.loc[matches, "_id"]:
            while identifier:
                included.add(identifier)
                identifier = by_id.at[identifier, "_parent"]
    children = {
        parent: frame.sort_values(sort_key, ascending=ascending, kind="stable")
        for parent, frame in display.groupby("_parent", sort=False)
    }
    identifiers = []

    def visit(parent):
        if parent not in children:
            return
        for _, row in children[parent].iterrows():
            if (query and row["_id"] not in included) or (not query and row["_depth"] >= depth):
                continue
            identifiers.append(row["_id"])
            visit(row["_id"])

    visit("")
    selected = by_id.loc[identifiers].reset_index()
    paths = {}
    for _, row in selected.iterrows():
        parent_path = paths.get(row["_parent"], "")
        paths[row["_id"]] = f"{parent_path} / {row['label']}" if parent_path else str(row["label"])
    selected["label"] = selected["_id"].map(paths)
    return selected


def detail_csv(display: pd.DataFrame, columns: list[tuple[str, str]]) -> bytes:
    export = display[[key for key, _ in columns]].copy()
    for key in export.select_dtypes(include=["object", "string"]).columns:
        export[key] = export[key].map(
            lambda value: "'" + value if isinstance(value, str) and re.match(r"^[=+@\-\t\r]", value) else value
        )
    export.columns = [label for _, label in columns]
    return export.to_csv(index=False, sep=";").encode("utf-8-sig")


def detail_page_html(rows: pd.DataFrame, columns: list[tuple[str, str]],
                     totals: dict, height: int, levels: int) -> str:
    def cells(row, indent=0):
        values = []
        for key, _ in columns:
            value = row[key]
            if key == "label":
                text = escape(str(value))
            elif key in ["calls", "contacts"]:
                text = f"{value:,.0f}"
            elif key == "rate":
                text = f"{value:.2f}%"
            else:
                text = f"{value:.1f}"
            padding = f' style="padding-left:{16 + indent * 48}px"' if key == "label" else ""
            values.append(f"<td{padding}>{text}</td>")
        return "".join(values)

    headers = "".join(f'<th scope="col">{escape(label)}</th>' for _, label in columns)
    body = []
    for _, row in rows.iterrows():
        depth = int(row["_depth"])
        row_class = "subtotal" if depth < levels - 1 else "detail-leaf"
        body.append(f'<tr class="{row_class}">{cells(row, depth)}</tr>')
    return (
        '<style>'
        '.coppel-detail-page{overflow:auto;border:1px solid #dbe6f0;border-radius:6px;background:white}'
        '.coppel-detail-page table{border-collapse:separate;border-spacing:0;width:100%;font:14px Arial,sans-serif;color:#14375f}'
        '.coppel-detail-page th{position:sticky;top:0;z-index:1;background:#23458f;color:#fff;padding:14px 16px;text-align:right;white-space:nowrap}'
        '.coppel-detail-page td{padding:12px 16px;border-bottom:1px solid #edf1f6;text-align:right;white-space:nowrap}'
        '.coppel-detail-page th:first-child,.coppel-detail-page td:first-child{text-align:left}'
        '.coppel-detail-page .subtotal td{font-weight:700;background:#edf3fb}'
        '.coppel-detail-page tbody tr:hover td{background:#e4eefb}'
        '.coppel-detail-page tfoot td{position:sticky;bottom:0;background:#23458f;color:#fff;font-weight:700}'
        '</style>'
        f'<div class="coppel-detail-page" style="height:{height}px" tabindex="0" role="region" aria-label="Detalle paginado">'
        f'<table><thead><tr>{headers}</tr></thead><tbody>{"".join(body)}</tbody>'
        f'<tfoot><tr>{cells(totals)}</tr></tfoot></table></div>'
    )


@st.fragment
def render_detail_page(display: pd.DataFrame, columns: list[tuple[str, str]], title: str,
                       totals: dict, height: int, page_size: int, levels: int) -> None:
    search_control, depth_control, sort_control, direction_control, page_control = st.columns([3, 1.5, 2, 2, 1])
    with search_control:
        query = st.text_input("Buscar en todo el detalle", key=f"{title}-search")
    with depth_control:
        depth = st.selectbox("Niveles de la jerarquía", range(1, levels + 1), index=min(1, levels - 1),
                             key=f"{title}-depth")
    with sort_control:
        sort_key = st.selectbox("Ordenar por", [key for key, _ in columns],
                                format_func=dict(columns).__getitem__, index=1, key=f"{title}-sort")
    with direction_control:
        direction = st.selectbox("Orden", ["Mayor a menor", "Menor a mayor"], key=f"{title}-direction")
    selected = select_detail_rows(display, query, depth, sort_key, direction == "Menor a mayor")
    page_count = max(1, (len(selected) + page_size - 1) // page_size)
    context = (query, depth, sort_key, direction, len(display), totals)
    context_key, page_key = f"{title}-context", f"{title}-page"
    if st.session_state.get(context_key) != context:
        st.session_state[page_key] = 1
        st.session_state[context_key] = context
    st.session_state[page_key] = min(st.session_state.get(page_key, 1), page_count)
    with page_control:
        page = st.number_input("Página", min_value=1, max_value=page_count, step=1, key=page_key)
    st.caption(f"Página {page} de {page_count} · {len(selected):,} filas · Total general sin cambios")
    page_rows = selected.iloc[(page - 1) * page_size:page * page_size]
    st.html(detail_page_html(page_rows, columns, totals, height, levels))
    if st.button("Preparar CSV completo", key=f"{title}-prepare-csv"):
        export = select_detail_rows(display, query, levels, sort_key, direction == "Menor a mayor")
        st.download_button("Descargar CSV completo", detail_csv(export, columns),
                           file_name="Coppel_detalle.csv", mime="text/csv",
                           key=f"{title}-download", on_click="ignore")


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
