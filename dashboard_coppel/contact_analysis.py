import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from interactive_table import render_interactive_table
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
    fig.update_layout(title="Composición de llamadas · campañas con más no contactos", barmode="stack")
    fig.update_xaxes(range=[0, 1], tickformat=".0%")
    fig.update_yaxes(tickmode="array", tickvals=list(range(len(selected))),
                     ticktext=[name if len(name) <= 40 else name[:37] + "..." for name in selected],
                     range=[len(selected) - .5, -.5])
    return fig


def render_contact_analysis(detail: pd.DataFrame, result: dict) -> None:
    if detail.empty:
        st.info("No hay llamadas para los filtros seleccionados.")
        return
    campaigns, outcomes = diagnostic_tables(detail)
    total = result["total"]
    contacts = result["contacts"]
    noncontacts = total - contacts
    high = campaigns.loc[campaigns["rate"].gt(95)]
    rest = campaigns.loc[~campaigns["rate"].gt(95)]
    high_calls = int(high["calls"].sum())
    rest_calls = int(rest["calls"].sum())
    rest_rate = rest["contacts"].sum() / rest_calls if rest_calls else 0
    st.markdown("### Qué explica el KPI")
    st.info(
        f"Contactabilidad ponderada: {contacts / total:.2%} = {contacts:,} contactos / {total:,} llamadas. "
        "No es el promedio simple de los porcentajes de las campañas."
    )
    st.markdown(
        f"**{len(high)} campañas superan el 95%**, pero representan **{high_calls / total:.2%} del volumen** "
        f"({high_calls:,} llamadas). El resto concentra **{rest_calls / total:.2%}** de las llamadas "
        f"y tiene una tasa conjunta de **{rest_rate:.2%}**."
    )
    normalized = outcomes["Call Outcome name"].str.strip().str.casefold()
    no_response = int(outcomes.loc[normalized.isin(NO_RESPONSE), "noncontacts"].sum())
    technical = int(outcomes.loc[normalized.isin(TECHNICAL), "noncontacts"].sum())
    unknown = int(outcomes.loc[outcomes["classification"].eq("Sin clasificación en maestro"), "calls"].sum())
    leader = campaigns.iloc[0]
    st.markdown(
        f"- **Sin respuesta, buzón u ocupado:** {no_response:,} llamadas sin contacto "
        f"(**{no_response / total:.2%} del total**). Es evidencia de falta de conexión con una persona; "
        "no demuestra por sí sola si se debe al horario, a la base o al reconocimiento de la llamada.\n"
        f"- **Resultados técnicos o de numeración:** {technical:,} no contactos "
        f"(**{technical / total:.2%} del total**), agrupando Network Failure, Protocol Failure, "
        "Invalid Number, Incomplete Logging, Drop y Cancel. Requieren revisar registros del marcador; "
        "un código de resultado no prueba una falla del proveedor.\n"
        f"- **Concentración:** la campaña **{leader['Campaign Name']}** aporta "
        f"**{leader['failure_share']:.2f}% de los no contactos**. Conviene priorizar por volumen perdido, no solo por tasa.\n"
        f"- **Cobertura del maestro:** {unknown:,} llamadas tienen resultados sin clasificación "
        f"(**{unknown / total:.2%} del total**); hoy no cuentan como contacto."
    )
    st.markdown("### Comparabilidad de las campañas")
    st.caption("Revise tipo de llamada, finalidad y criterios de registro antes de usar las campañas >95% como referencia.")
    types = detail.groupby(["Campaign Name", "Call Type"], as_index=False)[["calls", "contacts"]].sum()
    types["rate"] = types["contacts"] / types["calls"] * 100
    types["segment"] = types["Campaign Name"].isin(high["Campaign Name"]).map({True: ">95%", False: "Resto"})
    render_interactive_table(
        types.sort_values(["segment", "calls"], ascending=[True, False]),
        [("Campaign Name", "Campaña"), ("Call Type", "Tipo de llamada"), ("segment", "Grupo"),
         ("calls", "Total llamadas"), ("contacts", "Contactos"), ("rate", "% Contactabilidad")],
        "Comparabilidad de campañas",
        totals={"Campaign Name": "Total", "Call Type": "", "segment": "", "calls": total,
                "contacts": contacts, "rate": contacts / total * 100},
    )
    st.markdown("### Dónde se pierden más contactos")
    report_chart(failure_figure(detail, campaigns), max(420, min(len(campaigns), 15) * 32 + 160), left_margin=285)
    render_interactive_table(
        campaigns,
        [("Campaign Name", "Campaña"), ("calls", "Total llamadas"), ("share", "% del volumen"),
         ("contacts", "Contactos"), ("rate", "% Contactabilidad"), ("noncontacts", "No contactos"),
         ("failure_share", "% de todos los no contactos")],
        "Prioridad por volumen sin contacto",
        totals={"Campaign Name": "Total", "calls": total, "share": 100, "contacts": contacts,
                "rate": contacts / total * 100, "noncontacts": noncontacts,
                "failure_share": 100 if noncontacts else 0},
    )
    st.markdown("### Resultados que explican los no contactos")
    render_interactive_table(
        outcomes,
        [("Call Outcome name", "Resultado"), ("classification", "Clasificación maestro"),
         ("calls", "Total llamadas"), ("contacts", "Contactos"), ("noncontacts", "No contactos"),
         ("failure_share", "% de todos los no contactos")],
        "Diagnóstico por resultado",
        totals={"Call Outcome name": "Total", "classification": "", "calls": total,
                "contacts": contacts, "noncontacts": noncontacts, "failure_share": 100 if noncontacts else 0},
    )
    st.markdown("### Evolución mensual")
    monthly = presentation_metrics(result["month"])
    fig = go.Figure(go.Scatter(x=monthly["month"], y=monthly["rate"], mode="lines+markers+text",
                              text=[f"{value:.2%}" for value in monthly["rate"]],
                              textposition="top center", line=dict(color=LIGHT_BLUE)))
    fig.update_layout(title="Contactabilidad mensual · mismos filtros", showlegend=False)
    fig.update_yaxes(tickformat=".1%", rangemode="tozero")
    report_chart(fig)
    st.markdown("### Posibles causas y cómo confirmarlas")
    st.markdown(
        "1. **Base o intentos repetidos:** revisar teléfonos válidos, antigüedad y llamadas por número único. "
        "Este KPI mide intentos, no personas únicas; estos resúmenes no permiten medir repetición por teléfono.\n"
        "2. **Horario o estrategia de marcación:** contrastar los mapas de día/hora con campañas y probar franjas "
        "comparables con suficiente volumen; una asociación horaria no prueba causalidad.\n"
        "3. **Buzón / rechazo / identificación del DID:** revisar detección de contestadora, reputación y entrega "
        "por DID con el proveedor; validar con registros, no atribuirlo solo a la tasa.\n"
        "4. **Problemas técnicos o numeración:** auditar los códigos de terminación y ejemplos de llamadas "
        "Network Failure / Protocol Failure / Invalid Number.\n"
        "5. **Clasificación y mezcla operativa:** validar Mae_contacto y la finalidad de las campañas de alta tasa. "
        "No cambiar la clasificación para elevar artificialmente el KPI."
    )
    st.caption("Diagnóstico descriptivo de los datos filtrados. Las posibles causas requieren validación operativa; no son causas demostradas.")
