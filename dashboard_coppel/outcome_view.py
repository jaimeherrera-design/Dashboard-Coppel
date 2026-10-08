import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from detail_view import hierarchy_rows
from interactive_table import render_interactive_table
from report_view import LIGHT_BLUE, report_chart


def outcome_figure(table: pd.DataFrame) -> go.Figure:
    grouped = table.groupby("Call Outcome name", as_index=False)["calls"].sum().sort_values("calls", ascending=False)
    total = grouped["calls"].sum()
    shares = grouped["calls"] / (total if total else 1)
    positions = list(range(len(grouped)))
    fig = go.Figure(go.Bar(
        x=shares, y=positions, orientation="h", marker_color=LIGHT_BLUE,
        text=[f"{value:.2%}" for value in shares], textposition="outside",
        cliponaxis=False, textangle=0,
        customdata=list(zip(grouped["Call Outcome name"], grouped["calls"])),
        hovertemplate="%{customdata[0]}<br>Llamadas: %{customdata[1]:,.0f}<br>Participación: %{x:.2%}<extra></extra>",
    ))
    fig.update_layout(title="% TOTAL GENERAL POR RESULTADO DE LLAMADA", showlegend=False)
    fig.update_yaxes(tickmode="array", tickvals=positions, ticktext=grouped["Call Outcome name"], range=[len(grouped) - .5, -.5])
    fig.update_xaxes(tickformat=".0%", range=[0, max(float(shares.max()) * 1.18, .05) if not grouped.empty else 1])
    return fig


def render_outcomes(table: pd.DataFrame) -> None:
    if table.empty:
        st.info("No hay llamadas para los filtros seleccionados.")
        return
    height = max(600, table["Call Outcome name"].nunique() * 25 + 150)
    left, right = st.columns([.4, .6])
    with left:
        display = hierarchy_rows(table, ["Call Outcome name", "month", "date"])
        render_interactive_table(
            display, [("label", "Resultado / Mes / Fecha"), ("calls", "Total llamadas")],
            "Resultado llamadas", height=height, hierarchy=True, compact=True,
            totals={"label": "Total", "calls": int(table["calls"].sum())},
        )
    with right:
        report_chart(outcome_figure(table), height=height, left_margin=185)
