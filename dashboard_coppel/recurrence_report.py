"""Reporte PDF de recurrencia e intentos por teléfono (CLI y botón del panel lateral del dashboard)."""
from __future__ import annotations

import argparse
import sys
import tempfile
import time
from datetime import date, datetime
from io import BytesIO
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "Reportes"
EXCLUDED_CAMPAIGN_PATTERN = r"test|prueba"
INVALID_DATA_PATTERN = r"invalid number|fuera de servicio|no existe|equivocado|ya no vive"
CHRONIC_ATTEMPTS = 10
HEAVY_DAY_ATTEMPTS = 5
CURVE_MAX_ATTEMPT = 30
BATCH_ROWS = 1_000_000
COLUMNS = ["Phone", "Call end", "Campaign Name", "Call Outcome name"]

BLUE = "#23458f"
LIGHT_BLUE = "#7fa7e0"
GRAY = "#5b7087"
STATUS_COLORS = {"green": "#2e9e5b", "amber": "#f2a900", "red": "#d64545"}
STATUS_LABELS = {"green": "VERDE", "amber": "ÁMBAR", "red": "ROJO"}


def load_contact_outcomes(path: Path) -> set[str]:
    mapping = pd.read_excel(path)
    flag = mapping["Contacto / No Contacto"].fillna("").astype(str).str.strip().str.casefold()
    names = mapping["Call Outcome name"].fillna("").astype(str).str.strip().str.casefold()
    return set(names[flag.eq("contacto")])


def normalize_phones(values: pa.Array) -> pa.Array:
    """Normaliza a 10 dígitos (quita 52/521 y separadores); devuelve int64 con nulos si no es válido."""
    text = pc.cast(values, pa.string())
    text = pc.replace_substring_regex(pc.utf8_trim_whitespace(text), r"[\s()+-]", "")
    text = pc.replace_substring_regex(text, r"^(\d+)\.0+$", r"\1")
    length = pc.utf8_length(text)
    text = pc.if_else(
        pc.and_(pc.equal(length, 12), pc.starts_with(text, "52")), pc.utf8_slice_codeunits(text, 2), text,
    )
    text = pc.if_else(
        pc.and_(pc.equal(length, 13), pc.starts_with(text, "521")), pc.utf8_slice_codeunits(text, 3), text,
    )
    valid = pc.and_(pc.equal(pc.utf8_length(text), 10), pc.utf8_is_digit(text))
    return pc.cast(pc.if_else(valid, text, pa.scalar(None, pa.string())), pa.int64())


def read_calls(files: list[Path], contact_outcomes: set[str], start: date | None, end: date | None, log=print):
    """Lee las llamadas con las mismas exclusiones que el dashboard y devuelve arreglos compactos."""
    parts = {key: [] for key in ["phone", "ts", "contact", "invalid", "campaign"]}
    campaigns: dict[str, int] = {}
    totals = {"calls": 0, "invalid_phone_calls": 0}
    lower = pd.Timestamp(start) if start else None
    upper = pd.Timestamp(end) + pd.Timedelta(days=1) if end else None
    for path in files:
        reader = pq.ParquetFile(path)
        missing = set(COLUMNS).difference(reader.schema_arrow.names)
        if missing:
            raise ValueError(f"{path.name}: faltan columnas {', '.join(sorted(missing))}")
        log(f"Leyendo {path.name} ({reader.metadata.num_rows:,} filas)...")
        for batch in reader.iter_batches(batch_size=BATCH_ROWS, columns=COLUMNS):
            ends = pd.to_datetime(batch.column("Call end").to_pandas(), errors="coerce")
            campaign = pc.utf8_trim_whitespace(pc.fill_null(pc.cast(batch.column("Campaign Name"), pa.string()), "Sin dato"))
            keep = ends.notna().to_numpy() & ~pc.match_substring_regex(
                campaign, EXCLUDED_CAMPAIGN_PATTERN, ignore_case=True
            ).to_numpy(zero_copy_only=False)
            if lower is not None:
                keep &= (ends >= lower).to_numpy()
            if upper is not None:
                keep &= (ends < upper).to_numpy()
            totals["calls"] += int(keep.sum())
            phones = normalize_phones(batch.column("Phone"))
            valid_phone = pc.is_valid(phones).to_numpy(zero_copy_only=False)
            totals["invalid_phone_calls"] += int((keep & ~valid_phone).sum())
            keep &= valid_phone
            if not keep.any():
                continue
            outcome = pc.utf8_trim_whitespace(pc.fill_null(pc.cast(batch.column("Call Outcome name"), pa.string()), "Sin dato"))
            outcome_values = pd.Series(outcome.to_numpy(zero_copy_only=False)[keep]).str.casefold()
            encoded = pc.dictionary_encode(campaign)
            local_codes = np.array(
                [campaigns.setdefault(name, len(campaigns)) for name in encoded.dictionary.to_pylist()], dtype=np.int32,
            )
            parts["phone"].append(pc.fill_null(phones, 0).to_numpy()[keep])
            parts["ts"].append(ends.to_numpy()[keep].astype("datetime64[s]").astype(np.int64))
            parts["contact"].append(outcome_values.isin(contact_outcomes).to_numpy())
            parts["invalid"].append(outcome_values.str.contains(INVALID_DATA_PATTERN, regex=True).to_numpy())
            parts["campaign"].append(local_codes[encoded.indices.to_numpy(zero_copy_only=False)[keep]])
    arrays = {
        key: np.concatenate(value) if value else np.array([], dtype=dtype)
        for (key, value), dtype in zip(parts.items(), [np.int64, np.int64, bool, bool, np.int32])
    }
    names = np.empty(len(campaigns), dtype=object)
    for name, code in campaigns.items():
        names[code] = name
    return arrays, names, totals


def analyze(arrays: dict, campaign_names: np.ndarray) -> dict:
    phone, ts = arrays["phone"], arrays["ts"]
    if phone.size == 0:
        raise ValueError("No hay llamadas con teléfono válido en el periodo seleccionado.")
    order = np.lexsort((ts, phone))
    phone, ts = phone[order], ts[order]
    contact, invalid, campaign = arrays["contact"][order], arrays["invalid"][order], arrays["campaign"][order]
    rows = phone.size
    new_phone = np.r_[True, phone[1:] != phone[:-1]]
    starts = np.flatnonzero(new_phone)
    attempts = np.diff(np.r_[starts, rows])
    ordinal = np.arange(rows) - np.repeat(starts, attempts) + 1
    never_value = int(attempts.max()) + 1
    first_contact = np.minimum.reduceat(np.where(contact, ordinal, never_value), starts)
    contacted = first_contact < never_value
    contacts = np.add.reduceat(contact.astype(np.int64), starts)
    invalid_phone = np.maximum.reduceat(invalid.astype(np.int8), starts).astype(bool)

    day = ts // 86_400
    new_day = new_phone | np.r_[True, day[1:] != day[:-1]]
    day_starts = np.flatnonzero(new_day)
    day_attempts = np.diff(np.r_[day_starts, rows])
    days_dialed = np.add.reduceat(new_day.astype(np.int64), starts)
    phone_of_day = np.cumsum(new_phone)[day_starts] - 1
    max_per_day = np.zeros(starts.size, dtype=np.int64)
    np.maximum.at(max_per_day, phone_of_day, day_attempts)

    phones = starts.size
    never = ~contacted
    chronic = never & (attempts >= CHRONIC_ATTEMPTS)
    row_chronic = np.repeat(chronic, attempts)
    row_never = np.repeat(never, attempts)

    size = max(never_value, CURVE_MAX_ATTEMPT) + 1
    reached = np.minimum(attempts, first_contact)
    at_risk = np.cumsum(np.bincount(reached, minlength=size)[::-1])[::-1]
    hits = np.bincount(first_contact[contacted], minlength=size)
    curve_k = np.arange(1, CURVE_MAX_ATTEMPT + 1)
    curve_rate = np.divide(hits[curve_k], at_risk[curve_k], out=np.zeros(curve_k.size), where=at_risk[curve_k] > 0)
    cumulative_share = np.cumsum(hits[1:])[: CURVE_MAX_ATTEMPT] / max(int(contacted.sum()), 1)

    hours = (ts % 86_400) // 3_600
    hour_calls = np.bincount(hours, minlength=24)
    hour_contacts = np.bincount(hours[contact], minlength=24)

    first_contact_values = first_contact[contacted]
    segments = [
        ("Contactado al 1er intento", contacted & (first_contact == 1)),
        ("Contactado en 2 a 5 intentos", contacted & (first_contact >= 2) & (first_contact <= 5)),
        ("Contactado en 6 a 10 intentos", contacted & (first_contact >= 6) & (first_contact <= 10)),
        ("Contactado con más de 10 intentos", contacted & (first_contact > 10)),
        (f"Ilocalizable con menos de {CHRONIC_ATTEMPTS} intentos", never & ~chronic),
        (f"Ilocalizable crónico ({CHRONIC_ATTEMPTS}+ intentos)", chronic),
    ]
    segment_table = pd.DataFrame(
        [{"segmento": name, "telefonos": int(mask.sum()), "llamadas": int(attempts[mask].sum())} for name, mask in segments]
    )

    bins = [0, 1, 3, 5, 10, 20, 50, np.inf]
    labels = ["1", "2-3", "4-5", "6-10", "11-20", "21-50", "51+"]
    bucket = pd.cut(attempts, bins=bins, labels=labels)
    distribution = pd.crosstab(bucket, np.where(contacted, "Contactados", "Ilocalizables")).reindex(labels, fill_value=0)

    campaign_calls = np.bincount(campaign, minlength=campaign_names.size)
    campaign_chronic = np.bincount(campaign[row_chronic], minlength=campaign_names.size)
    campaign_table = pd.DataFrame({"campaña": campaign_names, "llamadas": campaign_calls, "llamadas_cronicos": campaign_chronic})
    campaign_table = campaign_table.loc[campaign_table["llamadas"].gt(0)]
    campaign_table["pct_cronicos"] = campaign_table["llamadas_cronicos"] / campaign_table["llamadas"]

    per_phone = pd.DataFrame({
        "telefono": phone[starts].astype(str), "intentos": attempts, "contactos": contacts,
        "intentos_hasta_contacto": np.where(contacted, first_contact, 0), "dias_marcados": days_dialed,
        "max_intentos_un_dia": max_per_day, "senal_dato_invalido": invalid_phone,
        "primera_llamada": pd.to_datetime(ts[starts], unit="s"),
        "ultima_llamada": pd.to_datetime(ts[starts + attempts - 1], unit="s"),
    })
    calls_after_contact = int((attempts[contacted] - first_contact[contacted]).sum())

    return {
        "calls": rows, "phones": phones, "contacted": int(contacted.sum()), "never": int(never.sum()),
        "chronic": int(chronic.sum()), "chronic_calls": int(attempts[chronic].sum()),
        "never_calls": int(row_never.sum()), "contacts": int(contacts.sum()),
        "attempts_mean": float(attempts.mean()), "attempts_median": float(np.median(attempts)),
        "first_contact_mean": float(first_contact_values.mean()) if first_contact_values.size else 0.0,
        "first_contact_median": float(np.median(first_contact_values)) if first_contact_values.size else 0.0,
        "first_contact_p90": float(np.percentile(first_contact_values, 90)) if first_contact_values.size else 0.0,
        "within_5": float((first_contact_values <= 5).mean()) if first_contact_values.size else 0.0,
        "invalid_phones": int(invalid_phone.sum()), "invalid_phone_calls": int(attempts[invalid_phone].sum()),
        "invalid_never": int((invalid_phone & never).sum()),
        "phone_days": int(day_starts.size), "heavy_days": int((day_attempts >= HEAVY_DAY_ATTEMPTS).sum()),
        "heavy_phones": int((max_per_day >= HEAVY_DAY_ATTEMPTS).sum()),
        "calls_after_contact": calls_after_contact,
        "curve": pd.DataFrame({"intento": curve_k, "tasa": curve_rate, "acumulado": cumulative_share, "en_riesgo": at_risk[curve_k]}),
        "hours": pd.DataFrame({"hora": np.arange(24), "llamadas": hour_calls, "contactos": hour_contacts}),
        "segments": segment_table, "distribution": distribution, "campaigns": campaign_table,
        "never_list": per_phone.loc[never].sort_values(["intentos", "dias_marcados"], ascending=False),
        "hard_contacts": per_phone.loc[contacted].sort_values("intentos_hasta_contacto", ascending=False).head(25),
        "period": (pd.to_datetime(ts.min(), unit="s"), pd.to_datetime(ts.max(), unit="s")),
    }


def classify(value: float, green_max: float, amber_max: float, higher_is_better: bool = False) -> str:
    if higher_is_better:
        return "green" if value >= green_max else "amber" if value >= amber_max else "red"
    return "green" if value < green_max else "amber" if value <= amber_max else "red"


def pct(value: float) -> str:
    return f"{value * 100:,.1f}%"


def num(value: float) -> str:
    return f"{value:,.0f}"


def build_insights(m: dict) -> list[dict]:
    phones, calls = m["phones"], m["calls"]
    never_share = m["never"] / phones
    chronic_call_share = m["chronic_calls"] / calls
    invalid_share = m["invalid_phones"] / phones
    heavy_share = m["heavy_days"] / max(m["phone_days"], 1)
    curve = m["curve"]
    low = curve.loc[(curve["intento"] > 1) & (curve["tasa"] < curve["tasa"].iloc[0] / 2), "intento"]
    cutoff = int(low.iloc[0]) if not low.empty else CURVE_MAX_ATTEMPT
    hours = m["hours"].loc[m["hours"]["llamadas"] >= max(calls * 0.01, 1)].copy()
    hours["tasa"] = hours["contactos"] / hours["llamadas"]
    best = hours.sort_values("tasa", ascending=False).head(3)["hora"].tolist() if not hours.empty else []
    best_text = ", ".join(f"{h:02d}:00" for h in sorted(best)) or "sin datos suficientes"
    top_campaign = m["campaigns"].sort_values("llamadas_cronicos", ascending=False).head(1)
    campaign_text = (
        f" La {'altura de mora' if m.get('by_mora') else 'campaña'} con más llamadas a crónicos es {top_campaign['campaña'].iloc[0]} "
        f"({num(top_campaign['llamadas_cronicos'].iloc[0])} llamadas)."
        if not top_campaign.empty and top_campaign["llamadas_cronicos"].iloc[0] > 0 else ""
    )
    return [
        {
            "status": classify(never_share, 0.30, 0.50),
            "title": "Clientes ilocalizables",
            "metric": pct(never_share),
            "finding": f"{num(m['never'])} de {num(phones)} teléfonos ({pct(never_share)}) nunca tuvieron un contacto en el periodo.",
            "action": f"Enviar estos teléfonos a enriquecimiento de datos o a canales alternos (SMS/WhatsApp). Mejores horas de contacto observadas: {best_text}.",
            "rule": "Verde < 30% · Ámbar 30–50% · Rojo > 50% de teléfonos sin contacto",
        },
        {
            "status": classify(m["first_contact_median"], 4, 6),
            "title": "Esfuerzo hasta el primer contacto",
            "metric": f"{m['first_contact_median']:,.0f} intentos",
            "finding": f"La mitad de los clientes contactados necesitó hasta {m['first_contact_median']:,.0f} marcaciones; el promedio es {m['first_contact_mean']:,.1f} y el 10% más difícil requirió {m['first_contact_p90']:,.0f} o más.",
            "action": "Priorizar en las primeras marcaciones a los teléfonos con historial de contacto y mover a otro horario los que no contestan.",
            "rule": "Verde ≤ 3 · Ámbar 4–6 · Rojo > 6 intentos (mediana)",
        },
        {
            "status": classify(chronic_call_share, 0.20, 0.40),
            "title": f"Llamadas a ilocalizables crónicos ({CHRONIC_ATTEMPTS}+ intentos)",
            "metric": pct(chronic_call_share),
            "finding": f"{num(m['chronic'])} teléfonos con {CHRONIC_ATTEMPTS} o más intentos sin contacto consumieron {num(m['chronic_calls'])} llamadas ({pct(chronic_call_share)} del total).{campaign_text}",
            "action": "Pausar o reducir frecuencia de marcación a estos teléfonos y reasignar la capacidad a teléfonos con mejor probabilidad.",
            "rule": "Verde < 20% · Ámbar 20–40% · Rojo > 40% de las llamadas",
        },
        {
            "status": classify(m["within_5"], 0.80, 0.60, higher_is_better=True),
            "title": "Rendimiento por número de intento",
            "metric": pct(m["within_5"]),
            "finding": f"El {pct(m['within_5'])} de los primeros contactos se logra en los primeros 5 intentos. Desde el intento {cutoff} la probabilidad de contacto cae a menos de la mitad que en el 1er intento.",
            "action": f"Evaluar un tope o una rotación de horario/canal a partir del intento {cutoff}.",
            "rule": "Verde ≥ 80% · Ámbar 60–80% · Rojo < 60% de contactos en ≤ 5 intentos",
        },
        {
            "status": classify(invalid_share, 0.03, 0.08),
            "title": "Calidad del dato telefónico",
            "metric": pct(invalid_share),
            "finding": f"{num(m['invalid_phones'])} teléfonos ({pct(invalid_share)}) registraron número inválido, fuera de servicio, no existe, equivocado o ya no vive ahí; acumulan {num(m['invalid_phone_calls'])} llamadas.",
            "action": "Depurar estos teléfonos de las bases de marcación y solicitar actualización de datos del cliente.",
            "rule": "Verde < 3% · Ámbar 3–8% · Rojo > 8% de teléfonos",
        },
        {
            "status": classify(heavy_share, 0.05, 0.15),
            "title": "Recurrencia en el mismo día",
            "metric": pct(heavy_share),
            "finding": f"En {pct(heavy_share)} de los días-teléfono se marcó {HEAVY_DAY_ATTEMPTS} o más veces al mismo número ({num(m['heavy_phones'])} teléfonos lo vivieron al menos un día). Además, {num(m['calls_after_contact'])} llamadas se hicieron después del primer contacto.",
            "action": "Definir un máximo de marcaciones diarias por teléfono y espaciar reintentos para reducir saturación y quejas.",
            "rule": f"Verde < 5% · Ámbar 5–15% · Rojo > 15% de días-teléfono con {HEAVY_DAY_ATTEMPTS}+ intentos",
        },
    ]


def chart(fig) -> BytesIO:
    import matplotlib.pyplot as plt

    buffer = BytesIO()
    fig.savefig(buffer, format="png", dpi=170, bbox_inches="tight")
    plt.close(fig)
    buffer.seek(0)
    return buffer


def build_charts(m: dict) -> dict[str, BytesIO]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter, PercentFormatter

    plt.rcParams.update({"font.size": 9, "axes.spines.top": False, "axes.spines.right": False, "axes.edgecolor": "#c9d4e0"})
    thousands = FuncFormatter(lambda value, _: f"{value:,.0f}")
    images = {}

    seg = m["segments"].iloc[::-1]
    colors = [STATUS_COLORS["red"], STATUS_COLORS["amber"], "#9bb6dd", LIGHT_BLUE, "#4f7cc4", BLUE][: len(seg)]
    fig, ax = plt.subplots(figsize=(8.6, 3.2))
    bars = ax.barh(seg["segmento"], seg["telefonos"] / m["phones"], color=colors)
    for bar, (_, row) in zip(bars, seg.iterrows()):
        ax.text(bar.get_width() + 0.005, bar.get_y() + bar.get_height() / 2,
                f"{row['telefonos'] / m['phones']:.1%} tel. · {row['llamadas'] / m['calls']:.1%} llamadas", va="center", fontsize=8)
    ax.xaxis.set_major_formatter(PercentFormatter(1.0))
    ax.set_xlim(0, max(seg["telefonos"] / m["phones"]) * 1.45)
    ax.set_title("¿Cuántos intentos se necesitan? Teléfonos por segmento", loc="left", color=BLUE, fontweight="bold")
    images["segments"] = chart(fig)

    curve = m["curve"]
    fig, ax = plt.subplots(figsize=(8.6, 3.2))
    ax.bar(curve["intento"], curve["tasa"], color=LIGHT_BLUE, label="Prob. de primer contacto en ese intento")
    ax.yaxis.set_major_formatter(PercentFormatter(1.0))
    ax2 = ax.twinx()
    ax2.plot(curve["intento"], curve["acumulado"], color=BLUE, marker="o", markersize=3, label="% acumulado de contactos logrados")
    ax2.yaxis.set_major_formatter(PercentFormatter(1.0))
    ax2.set_ylim(0, 1.05)
    ax2.spines["right"].set_visible(True)
    ax.set_xlabel("Número de intento al mismo teléfono")
    ax.set_title("Probabilidad de contacto por número de intento", loc="left", color=BLUE, fontweight="bold")
    lines = ax.get_legend_handles_labels()[0] + ax2.get_legend_handles_labels()[0]
    ax.legend(lines, [line.get_label() for line in lines], loc="center right", fontsize=8, frameon=False)
    images["curve"] = chart(fig)

    dist = m["distribution"]
    fig, ax = plt.subplots(figsize=(8.6, 3.0))
    x = np.arange(len(dist.index))
    width = 0.4
    for offset, column, color in [(-width / 2, "Contactados", BLUE), (width / 2, "Ilocalizables", STATUS_COLORS["red"])]:
        values = dist[column] if column in dist else np.zeros(len(x))
        ax.bar(x + offset, values, width, label=column, color=color)
    ax.set_xticks(x, dist.index)
    ax.yaxis.set_major_formatter(thousands)
    ax.set_xlabel("Intentos por teléfono en el periodo")
    ax.set_ylabel("Teléfonos")
    ax.legend(frameon=False)
    ax.set_title("Distribución de teléfonos por cantidad de intentos", loc="left", color=BLUE, fontweight="bold")
    images["distribution"] = chart(fig)

    hours = m["hours"].loc[m["hours"]["llamadas"] > 0].copy()
    hours["tasa"] = hours["contactos"] / hours["llamadas"]
    fig, ax = plt.subplots(figsize=(8.6, 3.0))
    ax.bar(hours["hora"], hours["llamadas"], color="#d7e3f4", label="Llamadas")
    ax.yaxis.set_major_formatter(thousands)
    ax2 = ax.twinx()
    ax2.plot(hours["hora"], hours["tasa"], color=STATUS_COLORS["green"], marker="o", markersize=3, label="% de contacto")
    ax2.yaxis.set_major_formatter(PercentFormatter(1.0))
    ax2.spines["right"].set_visible(True)
    ax.set_xticks(hours["hora"], [f"{h:02d}" for h in hours["hora"]])
    ax.set_xlabel("Hora del día")
    lines = ax.get_legend_handles_labels()[0] + ax2.get_legend_handles_labels()[0]
    ax.legend(lines, [line.get_label() for line in lines], loc="upper left", fontsize=8, frameon=False)
    ax.set_title("¿A qué hora contestan? Llamadas y % de contacto por hora", loc="left", color=BLUE, fontweight="bold")
    images["hours"] = chart(fig)
    return images


def build_pdf(m: dict, insights: list[dict], output: Path, sources: list[str], csv_name: str) -> None:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    styles = getSampleStyleSheet()
    base = ParagraphStyle("base", parent=styles["Normal"], fontName="Helvetica", fontSize=8.5, leading=11)
    small = ParagraphStyle("small", parent=base, fontSize=7.5, leading=9.5, textColor=colors.HexColor(GRAY))
    h1 = ParagraphStyle("h1", parent=base, fontName="Helvetica-Bold", fontSize=18, leading=22, textColor=colors.HexColor(BLUE))
    h2 = ParagraphStyle("h2", parent=base, fontName="Helvetica-Bold", fontSize=12, leading=15, textColor=colors.HexColor(BLUE), spaceBefore=6, spaceAfter=4)
    white_bold = ParagraphStyle("wb", parent=base, fontName="Helvetica-Bold", textColor=colors.white, alignment=TA_CENTER)
    kpi_value = ParagraphStyle("kv", parent=base, fontName="Helvetica-Bold", fontSize=14, leading=17, alignment=TA_CENTER, textColor=colors.HexColor(BLUE))
    kpi_label = ParagraphStyle("kl", parent=small, alignment=TA_CENTER)
    width = letter[0] - 3 * cm
    header = colors.HexColor(BLUE)

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.HexColor(GRAY))
        canvas.drawString(1.5 * cm, 1 * cm, "Coppel · Contact Center Intelligence · Análisis de recurrencia e intentos")
        canvas.drawRightString(letter[0] - 1.5 * cm, 1 * cm, f"Página {doc.page}")
        canvas.restoreState()

    header_cell = ParagraphStyle("hc", parent=white_bold, fontSize=7.5, leading=9)

    def table(data, widths, align_right_from=1):
        data = [[Paragraph(str(value), header_cell) for value in data[0]], *data[1:]]
        styled = Table(data, colWidths=widths, repeatRows=1)
        styled.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), header), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("FONTSIZE", (0, 0), (-1, -1), 7.5),
            ("ALIGN", (align_right_from, 1), (-1, -1), "RIGHT"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f6fb")]),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d5dfeb")),
            ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        return styled

    start, end = m["period"]
    story = [
        Paragraph("Análisis de recurrencia e intentos por teléfono", h1),
        Paragraph(f"Periodo: {start:%d/%m/%Y} al {end:%d/%m/%Y} · Generado: {datetime.now():%d/%m/%Y %H:%M} · Fuentes: {', '.join(sources)}", small),
        Spacer(1, 8),
    ]
    kpis = [
        (num(m["phones"]), "Teléfonos únicos marcados"), (num(m["calls"]), "Llamadas analizadas"),
        (f"{m['attempts_mean']:,.1f}", "Intentos promedio por teléfono"), (pct(m["contacted"] / m["phones"]), "Teléfonos contactados"),
        (pct(m["never"] / m["phones"]), "Teléfonos ilocalizables"), (f"{m['first_contact_mean']:,.1f}", "Intentos promedio hasta contacto"),
    ]
    kpi_cells = [[Paragraph(value, kpi_value) for value, _ in kpis], [Paragraph(label, kpi_label) for _, label in kpis]]
    kpi_table = Table(kpi_cells, colWidths=[width / 6] * 6)
    kpi_table.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#d5dfeb")), ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d5dfeb")),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f7f9fc")), ("TOPPADDING", (0, 0), (-1, -1), 6),
    ]))
    story += [kpi_table, Spacer(1, 10), Paragraph("6 insights semaforizados", h2)]

    rows = [[Paragraph("Estado", white_bold), Paragraph("Insight", white_bold), Paragraph("Qué encontramos", white_bold), Paragraph("Qué hacer", white_bold)]]
    status_styles = []
    for index, item in enumerate(insights, start=1):
        rows.append([
            Paragraph(f"<b>{STATUS_LABELS[item['status']]}</b><br/>{item['metric']}", ParagraphStyle("st", parent=white_bold, fontSize=8)),
            Paragraph(f"<b>{index}. {item['title']}</b><br/><font size=6.5 color='{GRAY}'>{item['rule']}</font>", base),
            Paragraph(item["finding"], base), Paragraph(item["action"], base),
        ])
        status_styles.append(("BACKGROUND", (0, index), (0, index), colors.HexColor(STATUS_COLORS[item["status"]])))
    insight_table = Table(rows, colWidths=[2.2 * cm, 4.2 * cm, (width - 6.4 * cm) * 0.55, (width - 6.4 * cm) * 0.45], repeatRows=1)
    insight_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), header), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d5dfeb")), ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4), *status_styles,
    ]))
    story += [insight_table, Spacer(1, 6), Paragraph(
        "<b>Cómo leer este reporte.</b> Cada fila de llamada es un intento. Un teléfono es <b>contactado</b> si al menos una llamada "
        "tuvo un resultado marcado como \"Contacto\" en Mae_contacto.xlsx; es <b>ilocalizable</b> si no tuvo ningún contacto en el periodo. "
        f"<b>Intentos hasta contacto</b> cuenta las marcaciones en orden cronológico hasta la primera llamada con contacto (incluida). "
        f"Se usan las mismas exclusiones del dashboard (campañas test/prueba y fechas inválidas); los teléfonos se normalizan a 10 dígitos. "
        f"{num(m['invalid_phone_calls_source'])} llamadas sin teléfono válido quedan fuera del análisis por teléfono.", small)]

    images = build_charts(m)
    story += [PageBreak(), Paragraph("Segmentación de teléfonos y esfuerzo de marcación", h2),
              Image(images["segments"], width=width, height=width * 3.2 / 8.6 * 0.98), Spacer(1, 6),
              Image(images["curve"], width=width, height=width * 3.2 / 8.6 * 0.98), Spacer(1, 6),
              Image(images["distribution"], width=width, height=width * 3.0 / 8.6 * 0.98)]
    seg_rows = [["Segmento", "Teléfonos", "% teléfonos", "Llamadas", "% llamadas"]] + [
        [row.segmento, num(row.telefonos), pct(row.telefonos / m["phones"]), num(row.llamadas), pct(row.llamadas / m["calls"])]
        for row in m["segments"].itertuples()
    ]
    story += [PageBreak(), Paragraph("Detalle por segmento", h2), table(seg_rows, [6.5 * cm] + [(width - 6.5 * cm) / 4] * 4),
              Spacer(1, 8), Image(images["hours"], width=width, height=width * 3.0 / 8.6 * 0.98)]
    camps = m["campaigns"].sort_values("llamadas_cronicos", ascending=False).head(12)
    label, group = ("Altura de mora", "% de la altura") if m.get("by_mora") else ("Campaña", "% de la campaña")
    if m.get("by_mora"):
        from mora import sort_by_mora

        camps = sort_by_mora(camps, "campaña")
    camp_rows = [[label, "Llamadas", f"Llamadas a crónicos ({CHRONIC_ATTEMPTS}+)", group]] + [
        [row.campaña, num(row.llamadas), num(row.llamadas_cronicos), pct(row.pct_cronicos)] for row in camps.itertuples()
    ]
    title = "Alturas de mora con llamadas a ilocalizables crónicos" if m.get("by_mora") else "Campañas con más llamadas a ilocalizables crónicos"
    story += [Spacer(1, 6), KeepTogether([Paragraph(title, h2),
                                          table(camp_rows, [7.5 * cm] + [(width - 7.5 * cm) / 3] * 3)])]

    def phone_rows(frame, extra):
        data = [["Teléfono", "Intentos", extra[0], "Días marcados", "Máx. en un día", "Primera llamada", "Última llamada", "Dato inválido"]]
        for row in frame.itertuples():
            data.append([row.telefono, num(row.intentos), num(getattr(row, extra[1])), num(row.dias_marcados), num(row.max_intentos_un_dia),
                         f"{row.primera_llamada:%d/%m/%Y}", f"{row.ultima_llamada:%d/%m/%Y}", "Sí" if row.senal_dato_invalido else "No"])
        return data

    widths = [2.6 * cm] + [(width - 2.6 * cm) / 7] * 7
    story += [PageBreak(), Paragraph("Top 25 clientes ilocalizables por número de intentos", h2),
              Paragraph(f"Teléfonos que nunca tuvieron contacto en el periodo. La lista completa de ilocalizables crónicos ({CHRONIC_ATTEMPTS}+ intentos) está en {csv_name}.", small),
              Spacer(1, 4), table(phone_rows(m["never_list"].head(25), ("Contactos", "contactos")), widths),
              Spacer(1, 10), Paragraph("Top 25 clientes que requirieron más marcaciones hasta encontrar el contacto", h2),
              table(phone_rows(m["hard_contacts"], ("Intentos hasta contacto", "intentos_hasta_contacto")), widths)]

    output.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(output), pagesize=letter, leftMargin=1.5 * cm, rightMargin=1.5 * cm, topMargin=1.3 * cm, bottomMargin=1.6 * cm,
                            title="Análisis de recurrencia e intentos por teléfono", author="Coppel Contact Center Intelligence")
    doc.build(story, onFirstPage=footer, onLaterPages=footer)


def discover_sources(local: bool) -> tuple[list[Path], Path]:
    if local:
        cache = ROOT / ".drive_cache"
        latest: dict[str, Path] = {}
        for path in sorted(cache.rglob("*.parquet"), key=lambda item: item.stat().st_mtime):
            latest[path.name.casefold()] = path
        masters = sorted(cache.rglob("Mae_contacto.xlsx"), key=lambda item: item.stat().st_mtime)
        if not latest or not masters:
            raise ValueError("No hay Parquet o Mae_contacto.xlsx en .drive_cache. Ejecute sin --local para sincronizar Drive.")
        return sorted(latest.values(), key=lambda item: item.name.casefold()), masters[-1]
    from drive_sources import discover_drive_sources

    sources = discover_drive_sources(ROOT)
    return [Path(signature[0]) for signature in sources.parquet], Path(sources.masters["Mae_contacto.xlsx"][0])


def group_by_mora(campaigns: pd.DataFrame, mora: Path) -> pd.DataFrame:
    """Agrupa la tabla de campañas por la altura de mora de Mae_Mora.xlsx."""
    from mora import map_campaigns, read_mora_master, set_order

    mapping, order = read_mora_master(mora)
    set_order(order)
    table = campaigns.assign(campaña=map_campaigns(campaigns["campaña"], mapping, EXCLUDED_CAMPAIGN_PATTERN))
    table = table.groupby("campaña", as_index=False)[["llamadas", "llamadas_cronicos"]].sum()
    table["pct_cronicos"] = table["llamadas_cronicos"] / table["llamadas"]
    return table


def generate_report(files: list[Path], mapping: Path, start: date | None, end: date | None,
                    csv_name: str = "ilocalizables_cronicos.csv", log=print, mora: Path | None = None) -> tuple[bytes, bytes]:
    """Genera el PDF y el CSV de ilocalizables crónicos en memoria."""
    arrays, campaign_names, totals = read_calls(files, load_contact_outcomes(mapping), start, end, log=log)
    metrics = analyze(arrays, campaign_names)
    del arrays
    if mora is not None:
        metrics["campaigns"] = group_by_mora(metrics["campaigns"], mora)
        metrics["by_mora"] = True
    metrics["invalid_phone_calls_source"] = totals["invalid_phone_calls"]
    chronic = metrics["never_list"].loc[metrics["never_list"]["intentos"] >= CHRONIC_ATTEMPTS]
    csv_data = chronic.drop(columns=["contactos", "intentos_hasta_contacto"]).to_csv(index=False, sep=";").encode("utf-8-sig")
    with tempfile.TemporaryDirectory() as folder:
        output = Path(folder) / "reporte.pdf"
        build_pdf(metrics, build_insights(metrics), output, [path.name for path in files], csv_name)
        return output.read_bytes(), csv_data


def main(argv: list[str] | None = None) -> Path:
    parser = argparse.ArgumentParser(description="Genera el PDF de recurrencia e intentos por teléfono.")
    parser.add_argument("--desde", type=date.fromisoformat, help="Fecha inicial AAAA-MM-DD (opcional)")
    parser.add_argument("--hasta", type=date.fromisoformat, help="Fecha final AAAA-MM-DD (opcional)")
    parser.add_argument("--local", action="store_true", help="Usar .drive_cache sin sincronizar Google Drive")
    parser.add_argument("--salida", type=Path, help="Ruta del PDF de salida")
    args = parser.parse_args(argv)
    began = time.time()
    files, mapping = discover_sources(args.local)
    print(f"Fuentes: {', '.join(path.name for path in files)}")
    arrays, campaign_names, totals = read_calls(files, load_contact_outcomes(mapping), args.desde, args.hasta)
    print(f"Llamadas en periodo: {totals['calls']:,} · sin teléfono válido: {totals['invalid_phone_calls']:,}. Analizando...")
    metrics = analyze(arrays, campaign_names)
    del arrays
    metrics["invalid_phone_calls_source"] = totals["invalid_phone_calls"]
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    output = args.salida or OUTPUT_DIR / f"Recurrencia_Intentos_{stamp}.pdf"
    csv_path = output.with_name(output.stem + "_ilocalizables_cronicos.csv")
    chronic = metrics["never_list"].loc[metrics["never_list"]["intentos"] >= CHRONIC_ATTEMPTS]
    output.parent.mkdir(parents=True, exist_ok=True)
    chronic.drop(columns=["contactos", "intentos_hasta_contacto"]).to_csv(csv_path, index=False, sep=";", encoding="utf-8-sig")
    insights = build_insights(metrics)
    build_pdf(metrics, insights, output, [path.name for path in files], csv_path.name)
    print(f"PDF: {output}\nCSV: {csv_path}\nListo en {time.time() - began:,.0f} s")
    return output


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, pa.ArrowException) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
