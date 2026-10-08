from html import escape

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st
from interactive_table import render_interactive_table


BLUE = "#23458f"
LIGHT_BLUE = "#168cff"
GREEN = "#20b34b"
RED = "#ff595e"
WEEKDAYS = ["Lun", "Mar", "Mie", "Jue", "Vie", "Sab", "Dom"]
PLOT_CONFIG = {"displayModeBar": True, "displaylogo": False}


def presentation_metrics(table: pd.DataFrame) -> pd.DataFrame:
    table = table.copy()
    denominator = table["calls"].replace(0, 1)
    table["rate"] = table["contacts"] / denominator
    table["tmo"] = (table["talk_sum"] + table["wrap_sum"]) / denominator
    table["idle"] = table["wait_sum"] / denominator
    return table


def report_chart(fig: go.Figure, height: int = 320, left_margin: int = 40) -> None:
    fig.update_layout(
        template="plotly_white",
        height=height,
        paper_bgcolor="white",
        plot_bgcolor="white",
        font=dict(family="Arial, sans-serif", size=11, color="#626873"),
        title=dict(x=.5, xanchor="center", font=dict(size=13, color="#626873")),
        margin=dict(l=left_margin, r=38, t=90, b=50),
        legend=dict(orientation="h", x=.5, xanchor="center", y=-.2),
        hovermode="closest",
    )
    fig.update_xaxes(showgrid=False, zeroline=False)
    fig.update_yaxes(gridcolor="#f0f1f4", zeroline=False)
    st.plotly_chart(fig, use_container_width=True, config=PLOT_CONFIG)


def report_table(table: pd.DataFrame, dimension: str, title: str, total: dict, performance: bool = False) -> None:
    table = presentation_metrics(table)
    columns = [(dimension, title), ("calls", "Total llamadas"), ("contacts", "Llamadas contactadas"), ("rate", "% Contactabilidad")]
    if performance:
        columns += [("tmo", "TMO (s)"), ("idle", "Idle (s)")]
    display = table[[key for key, _ in columns]].copy()
    display["rate"] *= 100
    calls = total["total"]
    denominator = calls if calls else 1
    totals = {
        dimension: "Total", "calls": calls, "contacts": total["contacts"],
        "rate": total["contacts"] / denominator * 100,
        "tmo": (total["talk_sum"] + total["wrap_sum"]) / denominator,
        "idle": total["wait_sum"] / denominator,
    }
    render_interactive_table(display, columns, title, totals=totals)


def heat_table(table: pd.DataFrame, dimension: str, order: list, title: str) -> None:
    if table.empty:
        st.info("No hay datos para el mapa de calor.")
        return
    calls = table.pivot(index="hour", columns=dimension, values="calls").reindex(index=range(24), columns=order).fillna(0)
    contacts = table.pivot(index="hour", columns=dimension, values="contacts").reindex(index=range(24), columns=order).fillna(0)
    rates = contacts.div(calls.where(calls.ne(0)))
    valid = rates.stack().dropna()
    low, high = (float(valid.quantile(.1)), float(valid.quantile(.9))) if not valid.empty else (0.0, 0.0)

    def cell(rate):
        if pd.isna(rate):
            return '<td class="no-data">—</td>'
        position = min(max((float(rate) - low) / (high - low), 0), 1) if high > low else .5
        if position <= .5:
            red, green, blue = 255, round(133 + 230 * position), round(126 + 162 * position)
        else:
            red, green, blue = round(255 - 332 * (position - .5)), round(248 - 76 * (position - .5)), round(207 - 154 * (position - .5))
        return f'<td style="background:rgb({red},{green},{blue})">{rate:.2%}</td>'

    header = "".join(f"<th>{escape(str(label))}</th>" for label in order)
    rows = "".join(
        f"<tr><th>{hour:02d}</th>" + "".join(cell(rates.loc[hour, label]) for label in order) + "</tr>"
        for hour in range(24)
    )
    totals = contacts.sum().div(calls.sum().where(calls.sum().ne(0)))
    footer = "".join(f"<td>{rate:.2%}</td>" if pd.notna(rate) else "<td>—</td>" for rate in totals)
    st.markdown(
        f'<div class="report-panel-title">{escape(title)}</div>'
        f'<div class="report-heat-scroll"><table class="report-table report-heat"><thead><tr><th>Hora</th>{header}</tr></thead>'
        f'<tbody>{rows}</tbody><tfoot><tr><th>Total</th>{footer}</tr></tfoot></table></div>',
        unsafe_allow_html=True,
    )


def rate_line(table: pd.DataFrame, dimension: str, title: str, average: float, order=None) -> None:
    table = presentation_metrics(table)
    if order is not None:
        table = table.set_index(dimension).reindex(order).reset_index()
    else:
        table = table.sort_values(dimension)
    fig = go.Figure(go.Scatter(
        x=table[dimension], y=table["rate"], mode="lines+markers+text",
        text=[f"{value:.2%}" if pd.notna(value) else "" for value in table["rate"]],
        textposition="top center", line=dict(color=LIGHT_BLUE, width=2),
        marker=dict(size=5), name="Contactabilidad", connectgaps=False,
        hovertemplate="%{x}<br>Contactabilidad: %{y:.2%}<extra></extra>",
    ))
    fig.add_hline(y=average, line_color=LIGHT_BLUE, line_dash="dot", line_width=1)
    fig.update_layout(title=title, showlegend=False)
    fig.update_yaxes(tickformat=".1%")
    rates = table["rate"].dropna()
    if not rates.empty:
        low = min(float(rates.min()), average)
        high = max(float(rates.max()), average)
        padding = max((high - low) * .18, .001)
        fig.update_yaxes(range=[max(0, low - padding), high + padding])
    if order is not None:
        fig.update_xaxes(type="category")
    report_chart(fig, 344)


def campaign_comparison(campaigns: pd.DataFrame, total: int) -> go.Figure:
    plotted = presentation_metrics(campaigns).sort_values("calls", ascending=False).head(15)
    positions = list(range(len(plotted)))
    labels = plotted["Campaign Name"].astype(str)
    short_labels = labels.map(lambda text: text if len(text) <= 35 else text[:32] + "...")
    fig = make_subplots(
        rows=1, cols=2, shared_yaxes=True, horizontal_spacing=.14,
        subplot_titles=("% Participación en llamadas", "% Contactabilidad"),
    )
    for column, values, name, color in [
        (1, plotted["calls"] / max(total, 1), "% Participación", LIGHT_BLUE),
        (2, plotted["rate"], "% Contactabilidad", BLUE),
    ]:
        fig.add_trace(go.Bar(
            y=positions, x=values, orientation="h", name=name, marker_color=color,
            width=.55, text=[f"{value:.2%}" for value in values],
            textposition="outside", textangle=0, cliponaxis=False,
            textfont=dict(size=11, color=color),
            customdata=list(zip(labels, plotted["calls"], plotted["contacts"])),
            hovertemplate=(
                "%{customdata[0]}<br>" + name + ": %{x:.2%}"
                "<br>Llamadas: %{customdata[1]:,.0f}<br>Contactos: %{customdata[2]:,.0f}<extra></extra>"
            ),
        ), row=1, col=column)
    participation_max = float((plotted["calls"] / max(total, 1)).max()) if not plotted.empty else 0
    fig.update_xaxes(range=[0, max(participation_max * 1.28, .05)], tickformat=".0%", row=1, col=1)
    fig.update_xaxes(range=[0, 1.22], tickvals=[0, .25, .5, .75, 1], tickformat=".0%", row=1, col=2)
    fig.update_yaxes(
        tickmode="array", tickvals=positions, ticktext=short_labels,
        range=[len(plotted) - .5, -.5], showgrid=False,
    )
    fig.update_layout(title="Peso operativo y efectividad por campaña", showlegend=False, bargap=.35)
    return fig


def render_report(result: dict, hourly_days: pd.DataFrame) -> None:
    if result["month"].empty:
        st.info("No hay llamadas para los filtros seleccionados.")
        return
    monthly = presentation_metrics(result["month"]).sort_values("month")
    campaigns = presentation_metrics(result["campaign"]).sort_values("calls", ascending=False)
    left, right = st.columns(2)
    with left:
        with st.container(border=False, key="report-month-table"):
            report_table(monthly, "month", "Año · Mes", result, performance=True)
    with right:
        with st.container(border=False, key="report-campaign-table"):
            report_table(campaigns, "Campaign Name", "Campaign Name", result, performance=True)
    left, right = st.columns(2)
    with left:
        with st.container(border=False, key="report-month-volume"):
            fig = go.Figure()
            fig.add_trace(go.Bar(
                x=monthly["month"], y=monthly["calls"], name="Total llamadas", marker_color=BLUE,
                text=monthly["calls"], texttemplate="%{text:,.0f}", textposition="outside",
            ))
            fig.add_trace(go.Scatter(
                x=monthly["month"], y=monthly["rate"], yaxis="y2", name="% Contactabilidad",
                mode="lines+markers+text", text=[f"{rate:.2%}" for rate in monthly["rate"]],
                textposition="top center", line=dict(color=GREEN, width=2), marker=dict(size=5),
            ))
            fig.update_layout(
                title="Total llamadas y % Contactabilidad por Año · Mes",
                yaxis2=dict(overlaying="y", side="right", tickformat=".1%", showgrid=False),
                xaxis=dict(type="category"),
            )
            report_chart(fig)
    with right:
        with st.container(border=False, key="report-month-times"):
            fig = go.Figure()
            for metric, label, color in [("tmo", "TMO", LIGHT_BLUE), ("idle", "Idle", RED)]:
                fig.add_trace(go.Scatter(
                    x=monthly["month"], y=monthly[metric], name=label, mode="lines+markers+text",
                    text=monthly[metric], texttemplate="%{text:.1f}", textposition="top center",
                    line=dict(color=color, width=2), marker=dict(size=5),
                ))
            fig.update_layout(title="TMO e Idle por Año · Mes", xaxis=dict(type="category"))
            fig.update_yaxes(title="Segundos")
            report_chart(fig)
    with st.container(border=False, key="report-campaign-chart"):
        fig = campaign_comparison(campaigns, result["total"])
        report_chart(fig, max(360, min(len(campaigns), 15) * 34 + 150), left_margin=260)
    with st.container(border=False, key="report-day-heat"):
        heat_table(hourly_days, "day_of_month", list(range(1, 32)), "MAPA DE CALOR · CONTACTABILIDAD · DÍA DEL MES Y HORA")
    left, right = st.columns(2)
    with left:
        with st.container(border=False, key="report-week-heat"):
            heat_table(result["heat"], "weekday", WEEKDAYS, "MAPA DE CALOR · CONTACTABILIDAD · DÍA DE LA SEMANA Y HORA")
    average = result["contacts"] / max(result["total"], 1)
    with right:
        with st.container(border=False, key="report-week-rate"):
            rate_line(result["weekday"], "weekday", "% CONTACTABILIDAD · DÍA DE LA SEMANA", average, WEEKDAYS)
        with st.container(border=False, key="report-day-rate"):
            rate_line(result["day_of_month"], "day_of_month", "% CONTACTABILIDAD · DÍA DEL MES", average)
