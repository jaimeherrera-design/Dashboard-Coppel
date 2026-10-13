from html import escape

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from interactive_table import render_interactive_table
from mora import sort_by_mora
from report_view import BLUE, LIGHT_BLUE, presentation_metrics, report_chart


NO_RESPONSE = {"no answer", "voicemail", "busy"}
TECHNICAL = {"network failure", "protocol failure", "invalid number", "incomplete logging", "drop", "cancel"}


def diagnostic_tables(detail: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    campaigns = detail.groupby("Campaign Name", as_index=False)[["calls", "contacts"]].sum()
    campaigns["rate"] = campaigns["contacts"] / campaigns["calls"] * 100
    campaigns["noncontacts"] = campaigns["calls"] - campaigns["contacts"]
    total = max(int(campaigns["calls"].sum()), 1)
    failed = max(int(campaigns["noncontacts"].sum()), 1)
    campaigns["share"] = campaigns["calls"] / total * 100
    campaigns["failure_share"] = campaigns["noncontacts"] / failed * 100
    campaigns = campaigns.sort_values("noncontacts", ascending=False)
    outcomes = detail.groupby("Call Outcome name", as_index=False)[["calls", "contacts"]].sum()
    outcomes["noncontacts"] = outcomes["calls"] - outcomes["contacts"]
    outcomes["failure_share"] = outcomes["noncontacts"] / failed * 100
    outcomes["classification"] = outcomes["contacts"].gt(0).map({True: "Contacto", False: "No contacto"})
    unknown = detail.loc[~detail["mapped"], "Call Outcome name"].unique()
    outcomes.loc[outcomes["Call Outcome name"].isin(unknown), "classification"] = "Sin clasificación en maestro"
    return campaigns, outcomes.sort_values("noncontacts", ascending=False)


def failure_figure(detail: pd.DataFrame, campaigns: pd.DataFrame) -> go.Figure:
    selected = campaigns.head(15)["Campaign Name"].tolist()
    detail = detail.loc[detail["Campaign Name"].isin(selected)].copy()
    normalized = detail["Call Outcome name"].str.strip().str.casefold()
    detail["reason"] = "Otros no contactos"
    detail.loc[normalized.isin(NO_RESPONSE), "reason"] = "Sin respuesta / buzón / ocupado"
    detail.loc[normalized.isin(TECHNICAL), "reason"] = "Resultados técnicos / numeración"
    detail.loc[detail["contacts"].gt(0), "reason"] = "Contactos"
    detail.loc[~detail["mapped"], "reason"] = "Sin clasificación en maestro"
    grouped = detail.groupby(["Campaign Name", "reason"])["calls"].sum()
    denominator = campaigns.set_index("Campaign Name")["calls"]
    fig = go.Figure()
    for reason, color in [
        ("Contactos", BLUE), ("Sin respuesta / buzón / ocupado", LIGHT_BLUE),
        ("Resultados técnicos / numeración", "#f6c400"), ("Otros no contactos", "#8298bd"),
        ("Sin clasificación en maestro", "#ff595e"),
    ]:
        values = [grouped.get((name, reason), 0) / denominator[name] for name in selected]
        fig.add_trace(go.Bar(
            x=values, y=list(range(len(selected))), orientation="h", name=reason,
            marker_color=color, customdata=selected,
            hovertemplate="%{customdata}<br>" + reason + ": %{x:.2%}<extra></extra>",
        ))
    fig.update_layout(title="Composición de llamadas por altura de mora", barmode="stack")
    fig.update_xaxes(range=[0, 1], tickformat=".0%")
    fig.update_yaxes(tickmode="array", tickvals=list(range(len(selected))),
                     ticktext=[name if len(name) <= 40 else name[:37] + "..." for name in selected],
                     range=[len(selected) - .5, -.5])
    return fig



LEVELS = {
    "red": ("🚨", "URGENTE"),
    "amber": ("⚠️", "VIGILAR"),
    "green": ("✅", "MANTENER"),
}


def level(value: float, red: float, amber: float) -> str:
    return "red" if value >= red else "amber" if value >= amber else "green"


def decision_insights(detail: pd.DataFrame, result: dict) -> list[dict]:
    campaigns, outcomes = diagnostic_tables(detail)
    total = max(int(result["total"]), 1)
    rate = result["contacts"] / total
    tmo = (result["talk_sum"] + result["wrap_sum"]) / total
    normalized = outcomes["Call Outcome name"].str.strip().str.casefold()
    no_response = int(outcomes.loc[normalized.isin(NO_RESPONSE), "noncontacts"].sum())
    technical = int(outcomes.loc[normalized.isin(TECHNICAL), "noncontacts"].sum())
    cards = []

    leader = campaigns.iloc[0]
    if leader["noncontacts"] > 0:
        cards.append({
            "status": level(leader["failure_share"], 25, 10),
            "title": "Altura de mora que más contactos pierde",
            "finding": f"{leader['Campaign Name']} concentra {leader['failure_share']:.1f}% de todas las llamadas sin contacto "
                       f"({int(leader['noncontacts']):,}) y solo contacta al {leader['rate']:.1f}%.",
            "do": "Revisar primero esta altura de mora: calidad de la base de teléfonos, horario de marcación y guion.",
            "dont": "No aumentar su volumen de marcación hasta corregirla; solo generará más llamadas perdidas.",
        })
    else:
        cards.append({
            "status": "green", "title": "Alturas de mora sin pérdidas relevantes",
            "finding": "Ninguna altura de mora acumula llamadas sin contacto en el periodo filtrado.",
            "do": "Mantener el seguimiento semanal por altura de mora.",
            "dont": "No cambiar la estrategia de marcación sin evidencia de deterioro.",
        })

    cards.append({
        "status": level(no_response / total, .5, .25),
        "title": "Llamadas que nadie contesta",
        "finding": f"{no_response / total:.1%} de todas las llamadas ({no_response:,}) terminan sin respuesta, en buzón u ocupado.",
        "do": "Marcar en las horas de mejor contacto y espaciar los reintentos al mismo número.",
        "dont": "No repetir llamadas seguidas al mismo número; satura al cliente y desgasta la base.",
    })

    cards.append({
        "status": level(technical / total, .05, .01),
        "title": "Fallas técnicas y números inválidos",
        "finding": f"{technical / total:.1%} de las llamadas ({technical:,}) fallan por red, protocolo, número inválido o corte.",
        "do": "Pedir al proveedor / TI el reporte de estos códigos y depurar los números inválidos de la base.",
        "dont": "No atribuirlo a los agentes ni seguir marcando números inválidos.",
    })

    hours = result["hour"].loc[result["hour"]["calls"] >= total * .01].copy()
    if not hours.empty:
        hours["rate"] = hours["contacts"] / hours["calls"]
        best, worst = hours.loc[hours["rate"].idxmax()], hours.loc[hours["rate"].idxmin()]
        cards.append({
            "status": "amber" if worst["rate"] < rate * .7 else "green",
            "title": "Mejor y peor hora para llamar",
            "finding": f"A las {int(best['hour']):02d}:00 se contacta al {best['rate']:.1%}; "
                       f"a las {int(worst['hour']):02d}:00 solo al {worst['rate']:.1%} (promedio {rate:.1%}).",
            "do": f"Concentrar más llamadas a las {int(best['hour']):02d}:00 y en las horas cercanas.",
            "dont": f"No cargar volumen fuerte a las {int(worst['hour']):02d}:00 solo porque hay agentes disponibles.",
        })

    campaign = result["campaign"].loc[result["campaign"]["calls"] >= total * .01].copy()
    if not campaign.empty and tmo:
        campaign["tmo"] = (campaign["talk_sum"] + campaign["wrap_sum"]) / campaign["calls"]
        campaign["rate"] = campaign["contacts"] / campaign["calls"]
        slow = campaign.loc[campaign["tmo"].idxmax()]
        gap = slow["tmo"] / tmo - 1
        cards.append({
            "status": level(gap, .5, .2),
            "title": "Tiempo por llamada (TMO) más alto",
            "finding": f"{slow['Campaign Name']} dura {slow['tmo']:.1f} s por llamada, {gap:.0%} más que el promedio ({tmo:.1f} s).",
            "do": "Revisar con supervisión el guion y el cierre de estas llamadas para liberar capacidad.",
            "dont": "No cortar llamadas efectivas solo para bajar el TMO; se pierden contactos.",
        })
        model = campaign.loc[campaign["rate"].idxmax()]
        cards.append({
            "status": "green",
            "title": "Altura de mora modelo a replicar",
            "finding": f"{model['Campaign Name']} logra {model['rate']:.1%} de contacto con {int(model['calls']):,} llamadas "
                       f"y {model['tmo']:.1f} s por llamada.",
            "do": "Copiar su horario, base y guion en las alturas de mora con menor contacto.",
            "dont": "No usarla como meta para alturas de mora de otro tipo o finalidad sin validarlo antes.",
        })
    fallback = {
        "status": "green", "title": "Sin volumen suficiente para comparar",
        "finding": "Con los filtros actuales no hay horas o alturas de mora con al menos 1% del volumen.",
        "do": "Ampliar el periodo o quitar filtros para obtener una comparación confiable.",
        "dont": "No tomar decisiones con muestras muy pequeñas.",
    }
    while len(cards) < 6:
        cards.append(dict(fallback))

    order = {"red": 0, "amber": 1, "green": 2}
    return sorted(cards[:6], key=lambda card: order[card["status"]])


def decision_card(card: dict) -> str:
    icon, tag = LEVELS[card["status"]]
    return (
        f'<div class="decision-card {card["status"]}">'
        f'<div class="decision-head"><span class="decision-icon">{icon}</span><span class="decision-tag">{tag}</span></div>'
        f'<div class="decision-title">{escape(card["title"])}</div>'
        f'<div class="decision-finding">{escape(card["finding"])}</div>'
        f'<div class="decision-do"><strong>✔ Qué hacer:</strong> {escape(card["do"])}</div>'
        f'<div class="decision-dont"><strong>✖ Qué no hacer:</strong> {escape(card["dont"])}</div>'
        '</div>'
    )


def render_contact_analysis(detail: pd.DataFrame, result: dict) -> None:
    if detail.empty:
        st.info("No hay llamadas para los filtros seleccionados.")
        return
    campaigns, outcomes = diagnostic_tables(detail)
    total = result["total"]
    contacts = result["contacts"]
    noncontacts = total - contacts
    cards = decision_insights(detail, result)
    st.markdown('<div class="section-title">1. Decisiones prioritarias</div>', unsafe_allow_html=True)
    unknown = int(outcomes.loc[outcomes["classification"].eq("Sin clasificación en maestro"), "calls"].sum())
    if unknown:
        st.markdown(
            f'<div class="decision-alert">🚨 <strong>Alerta de datos:</strong> {unknown:,} llamadas tienen un resultado '
            'que no está en Mae_contacto y hoy no cuentan como contacto. Pida clasificarlos en el maestro.</div>',
            unsafe_allow_html=True,
        )
    for row in range(0, len(cards), 3):
        columns = st.columns(3)
        for column, card in zip(columns, cards[row:row + 3]):
            with column:
                st.markdown(decision_card(card), unsafe_allow_html=True)

    st.markdown('<div class="section-title">2. Dónde se pierden los contactos</div>', unsafe_allow_html=True)
    report_chart(failure_figure(detail, sort_by_mora(campaigns)), max(420, min(len(campaigns), 15) * 32 + 160), left_margin=285)
    with st.expander("Ver detalle por altura de mora"):
        render_interactive_table(
            sort_by_mora(campaigns),
            [("Campaign Name", "Altura de mora"), ("calls", "Total llamadas"), ("share", "% del volumen"),
             ("contacts", "Contactos"), ("rate", "% Contactabilidad"), ("noncontacts", "No contactos"),
             ("failure_share", "% de todos los no contactos")],
            "Prioridad por volumen sin contacto",
            totals={"Campaign Name": "Total", "calls": total, "share": 100, "contacts": contacts,
                    "rate": contacts / total * 100, "noncontacts": noncontacts,
                    "failure_share": 100 if noncontacts else 0},
        )
    with st.expander("Ver detalle por resultado de llamada"):
        render_interactive_table(
            outcomes,
            [("Call Outcome name", "Resultado"), ("classification", "Clasificación maestro"),
             ("calls", "Total llamadas"), ("contacts", "Contactos"), ("noncontacts", "No contactos"),
             ("failure_share", "% de todos los no contactos")],
            "Diagnóstico por resultado",
            totals={"Call Outcome name": "Total", "classification": "", "calls": total,
                    "contacts": contacts, "noncontacts": noncontacts, "failure_share": 100 if noncontacts else 0},
        )

    st.markdown('<div class="section-title">3. Tendencia mensual</div>', unsafe_allow_html=True)
    monthly = presentation_metrics(result["month"])
    fig = go.Figure(go.Scatter(x=monthly["month"], y=monthly["rate"], mode="lines+markers+text",
                              text=[f"{value:.2%}" for value in monthly["rate"]],
                              textposition="top center", line=dict(color=LIGHT_BLUE)))
    fig.update_layout(title="Contactabilidad mensual · mismos filtros", showlegend=False)
    fig.update_yaxes(tickformat=".1%", rangemode="tozero")
    report_chart(fig)
    with st.expander("Ver comparabilidad de alturas de mora por tipo de llamada"):
        high = campaigns.loc[campaigns["rate"].gt(95), "Campaign Name"]
        types = detail.groupby(["Campaign Name", "Call Type"], as_index=False)[["calls", "contacts"]].sum()
        types["rate"] = types["contacts"] / types["calls"] * 100
        types["segment"] = types["Campaign Name"].isin(high).map({True: ">95%", False: "Resto"})
        render_interactive_table(
            sort_by_mora(types, before=["segment"], after=["Call Type"]),
            [("Campaign Name", "Altura de mora"), ("Call Type", "Tipo de llamada"), ("segment", "Grupo"),
             ("calls", "Total llamadas"), ("contacts", "Contactos"), ("rate", "% Contactabilidad")],
            "Comparabilidad de alturas de mora",
            totals={"Campaign Name": "Total", "Call Type": "", "segment": "", "calls": total,
                    "contacts": contacts, "rate": contacts / total * 100},
        )
