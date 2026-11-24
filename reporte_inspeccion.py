"""Reporte de inspecciones de uso de EPP y dotación: indicadores, Excel y PDF."""
import io
import os
from datetime import date, timedelta

import pandas as pd
import plotly.express as px
import streamlit as st
from fpdf import FPDF

import inspeccion
from comun import (
    BUCKET_FIRMAS,
    XLSX_MIME,
    a_excel,
    acceso,
    fecha_local,
    get_sb,
    traer_todo,
)

LOGO_NEGRO = "logonegro.png"

# Datos del formato oficial de inspección (cámbialos aquí cuando SST los confirme)
CODIGO_INSPECCION = "SST-HI-ED-FO-04"
VERSION_INSPECCION = "2"


# ---------- Datos ----------
@st.cache_data(ttl=60, show_spinner="Cargando calificaciones...")
def cargar_items():
    filas = traer_todo(
        lambda: get_sb().table("v_inspeccion_items").select("*")
        .order("inspeccion_id").order("item_id")
    )
    return pd.DataFrame(filas)


@st.cache_data(ttl=60, show_spinner=False)
def cargar_causas():
    filas = traer_todo(
        lambda: get_sb().table("v_inspeccion_causas").select("*")
        .order("inspeccion_id").order("codigo")
    )
    return pd.DataFrame(filas)


# ---------- Excel con la forma del formato físico ----------
def hoja_formato(insp, items, causas):
    """Una fila por inspección y una columna por ítem (C / NC / N/A)."""
    base = insp.set_index("inspeccion_id")
    if items.empty:
        columnas_items = []
        pivot = pd.DataFrame(index=base.index)
    else:
        orden = items.drop_duplicates("item_id").sort_values("orden")["item"].tolist()
        pivot = items.pivot_table(
            index="inspeccion_id", columns="item", values="estado", aggfunc="first"
        ).reindex(columns=orden)
        pivot = pivot.apply(lambda col: col.replace({"NA": "N/A"}))
        columnas_items = orden

    if causas.empty:
        causas_cod = pd.Series(dtype=str, name="Causas (códigos)")
    else:
        causas_cod = (
            causas.groupby("inspeccion_id")["codigo"]
            .apply(lambda s: ", ".join(str(c) for c in sorted(s)))
            .rename("Causas (códigos)")
        )

    cols = ["Fecha", "cedula", "trabajador", "cargo", "area"]
    extra = ["no_permitidos", "talla_zapato", "talla_bata", "talla_polo", "talla_jeans",
             "observaciones", "medidas", "inspector"]
    out = (
        base.reindex(columns=cols + extra)
        .join(pivot, how="left")
        .join(causas_cod, how="left")
        .reset_index()
    )
    out = out[
        ["inspeccion_id"] + cols + columnas_items
        + ["Causas (códigos)", "no_permitidos", "talla_zapato", "talla_bata",
           "talla_polo", "talla_jeans", "observaciones", "medidas", "inspector"]
    ]
    out = out.rename(columns={
        "inspeccion_id": "Inspección", "cedula": "Cédula", "trabajador": "Nombre",
        "cargo": "Cargo", "area": "Área", "no_permitidos": "Elementos no permitidos",
        "talla_zapato": "Talla zapato", "talla_bata": "Talla bata",
        "talla_polo": "Talla polo", "talla_jeans": "Talla jeans",
        "observaciones": "Observaciones", "medidas": "Medidas de control",
        "inspector": "Inspector",
    })
    return out.fillna("")


# ---------- PDF de una inspección ----------
def _txt(texto):
    """Compatibilidad de tildes y caracteres latinos en FPDF."""
    return str(texto or "").encode("latin-1", "replace").decode("latin-1")


class InspeccionPDF(FPDF):
    def header(self):
        y0, alto = 10, 24
        self.set_line_width(0.3)
        self.rect(10, y0, 40, alto)
        if os.path.exists(LOGO_NEGRO):
            self.image(LOGO_NEGRO, x=13, y=y0, w=24)

        self.rect(50, y0, 102, alto)
        self.set_xy(50, y0 + 2)
        self.set_font("Helvetica", "B", 10)
        self.cell(102, 4.5, _txt("KENZO JEANS S.A.S."), align="C", ln=True)
        self.set_x(50)
        self.set_font("Helvetica", "B", 7.5)
        self.cell(102, 4, _txt("FORMATO DE INSPECCIÓN DE USO DE ELEMENTOS DE"), align="C", ln=True)
        self.set_x(50)
        self.cell(102, 4, _txt("PROTECCIÓN PERSONAL Y DOTACIÓN"), align="C", ln=True)
        self.set_x(50)
        self.set_font("Helvetica", "", 7)
        self.cell(102, 4, _txt("SISTEMA DE GESTIÓN DE SEGURIDAD Y SALUD EN EL TRABAJO"), align="C", ln=True)

        self.rect(152, y0, 48, 8)
        self.rect(152, y0 + 8, 48, 8)
        self.rect(152, y0 + 16, 48, 8)
        self.set_font("Helvetica", "B", 7.5)
        self.set_xy(153, y0 + 1.5)
        self.cell(46, 5, _txt(f"Código: {CODIGO_INSPECCION}"), align="L")
        self.set_xy(153, y0 + 9.5)
        self.cell(46, 5, _txt(f"Versión: {VERSION_INSPECCION}"), align="L")
        self.set_xy(153, y0 + 17.5)
        self.set_font("Helvetica", "", 7.5)
        self.cell(46, 5, _txt(f"Página {self.page_no()} de {{nb}}"), align="L")
        self.set_y(y0 + alto + 5)

    def footer(self):
        self.set_y(-12)
        self.set_font("Helvetica", "I", 7)
        self.set_text_color(110, 110, 110)
        self.cell(
            0, 5,
            _txt("KENZO JEANS S.A.S. - Sistema de Gestión de Seguridad y Salud en el Trabajo (SG-SST)"),
            align="C",
        )
        self.set_text_color(0, 0, 0)


def _parrafo(pdf, texto, border="LR"):
    """multi_cell que deja el cursor en el margen izquierdo (permite encadenar párrafos)."""
    pdf.multi_cell(0, 5, texto, border=border)
    pdf.set_x(pdf.l_margin)


def _tabla_items(pdf, titulo, items):
    pdf.set_fill_color(235, 238, 242)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(130, 6.5, _txt(f" {titulo}"), border=1, fill=True)
    for etiqueta in ("C", "NC", "N/A"):
        pdf.cell(20, 6.5, etiqueta, border=1, align="C", fill=True)
    pdf.ln(6.5)
    pdf.set_font("Helvetica", "", 8.5)
    for it in items:
        estado = it["estado"]
        pdf.cell(130, 6, _txt(f" {it['insp_items']['nombre'][:70]}"), border=1)
        for clave in ("C", "NC", "NA"):
            marcado = estado == clave
            if marcado and clave == "NC":
                pdf.set_fill_color(255, 225, 225)
            pdf.cell(20, 6, "X" if marcado else "", border=1, align="C", fill=marcado and clave == "NC")
            pdf.set_fill_color(235, 238, 242)
        pdf.ln(6)
    pdf.ln(3)


def generar_pdf_inspeccion(d, firma_bytes=None):
    pdf = InspeccionPDF()
    pdf.alias_nb_pages()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=16)
    trab = d["trabajadores"]

    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(95, 6, _txt(f"INSPECCIÓN N.° {d['id']}"), align="L")
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(95, 6, _txt(f"Fecha y hora: {d['fecha_local']}"), align="R", ln=True)
    pdf.ln(2)

    pdf.set_fill_color(235, 238, 242)
    pdf.set_font("Helvetica", "B", 9.5)
    pdf.cell(0, 7, _txt(" 1. COLABORADOR A QUIEN SE LE REALIZA LA INSPECCIÓN"), border=1, ln=True, fill=True)
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(110, 7, _txt(f" Nombre: {trab['nombre']}"), border=1)
    pdf.cell(80, 7, _txt(f" Cédula: {trab['cedula']}"), border=1, ln=True)
    pdf.cell(110, 7, _txt(f" Cargo: {d.get('cargo') or 'N/A'}"), border=1)
    pdf.cell(80, 7, _txt(f" Área: {d.get('area') or 'N/A'}"), border=1, ln=True)
    pdf.ln(4)

    pdf.set_font("Helvetica", "B", 9.5)
    pdf.cell(0, 7, _txt(" 2. CALIFICACIÓN DE USO (C: Cumple · NC: No cumple · N/A: No aplica)"), border=1, ln=True, fill=True)
    items = sorted(d["inspeccion_items"], key=lambda x: x["insp_items"]["orden"])
    _tabla_items(pdf, "Elementos de protección personal", [i for i in items if i["insp_items"]["tipo"] == "EPP"])
    _tabla_items(pdf, "Dotación", [i for i in items if i["insp_items"]["tipo"] == "DOTACION"])

    pdf.set_font("Helvetica", "B", 9.5)
    pdf.cell(0, 7, _txt(" 3. HALLAZGOS"), border=1, ln=True, fill=True)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(0, 6, _txt(f" Resultado: {'CUMPLE' if d['cumple'] else 'NO CUMPLE'}"), border="LR", ln=True)
    pdf.set_font("Helvetica", "", 8.5)
    causas = sorted(d["inspeccion_causas"], key=lambda x: x["insp_causas"]["codigo"])
    if causas:
        lista = "\n".join(f"{c['insp_causas']['codigo']}. {c['insp_causas']['texto']}" for c in causas)
        _parrafo(pdf, _txt(f" Razón por la cual no usa los EPP o la dotación:\n{lista}"), "LR")
    no_perm = [n["insp_no_permitidos"]["nombre"] for n in d["inspeccion_no_permitidos"]]
    if no_perm:
        _parrafo(pdf, _txt(f" Elementos no permitidos hallados: {', '.join(no_perm)}"), "LR")
    tallas = [
        ("Zapato", d.get("talla_zapato")), ("Bata", d.get("talla_bata")),
        ("Polo", d.get("talla_polo")), ("Jeans", d.get("talla_jeans")),
    ]
    tallas_txt = " · ".join(f"{k}: {v}" for k, v in tallas if v)
    if tallas_txt:
        _parrafo(pdf, _txt(f" Tallas - {tallas_txt}"), "LR")
    _parrafo(pdf, _txt(f" Observaciones: {d.get('observaciones') or '-'}"), "LRB")
    pdf.ln(4)

    pdf.set_font("Helvetica", "B", 9.5)
    pdf.cell(0, 7, _txt(" 4. MEDIDAS DE CONTROL (SST y RR. HH.)"), border=1, ln=True, fill=True)
    pdf.set_font("Helvetica", "", 8.5)
    medidas = sorted(d["inspeccion_medidas"], key=lambda x: x["insp_medidas"]["codigo"])
    if medidas:
        lista = "\n".join(f"{m['insp_medidas']['codigo']}. {m['insp_medidas']['texto']}" for m in medidas)
        definida = next((m["definida_por"] for m in medidas if m.get("definida_por")), None)
        pie = f"\nDefinidas por: {definida}" if definida else ""
        _parrafo(pdf, _txt(f" {lista}{pie}"), "LRB")
    else:
        texto = "Cumple: no requiere medida." if d["cumple"] else "Pendiente de definición por SST y RR. HH."
        _parrafo(pdf, _txt(f" {texto}"), "LRB")
    pdf.ln(6)

    if pdf.get_y() > 235:
        pdf.add_page()
    y_firma = pdf.get_y()
    if firma_bytes:
        pdf.image(io.BytesIO(firma_bytes), x=33, y=y_firma, w=48, h=24)
    pdf.set_y(y_firma + 25)
    pdf.set_font("Helvetica", "B", 8.5)
    pdf.cell(95, 4.5, "________________________________________", align="C")
    pdf.cell(95, 4.5, "________________________________________", align="C", ln=True)
    pdf.cell(95, 4.5, _txt("FIRMA DEL COLABORADOR INSPECCIONADO"), align="C")
    pdf.cell(95, 4.5, _txt("INSPECTOR"), align="C", ln=True)
    pdf.set_font("Helvetica", "", 8)
    pdf.cell(95, 4.5, _txt(f"{trab['nombre']} · C.C. {trab['cedula']}"), align="C")
    pdf.cell(95, 4.5, _txt(d["inspector"]), align="C", ln=True)
    return bytes(pdf.output())


@st.cache_data(ttl=3600, show_spinner="Generando PDF...")
def pdf_inspeccion(inspeccion_id):
    d = (
        get_sb().table("inspecciones_uso")
        .select(
            "id,fecha_hora,inspector,observaciones,cumple,cargo,area,"
            "talla_zapato,talla_bata,talla_polo,talla_jeans,firma_path,"
            "trabajadores(nombre,cedula),"
            "inspeccion_items(estado,insp_items(nombre,tipo,orden)),"
            "inspeccion_causas(insp_causas(codigo,texto)),"
            "inspeccion_no_permitidos(insp_no_permitidos(nombre)),"
            "inspeccion_medidas(definida_por,insp_medidas(codigo,texto))"
        )
        .eq("id", inspeccion_id)
        .single()
        .execute()
        .data
    )
    d["fecha_local"] = fecha_local(d["fecha_hora"])
    firma = None
    if d.get("firma_path"):  # si falla la descarga, no se guarda en caché
        firma = get_sb().storage.from_(BUCKET_FIRMAS).download(d["firma_path"])
    return generar_pdf_inspeccion(d, firma), firma


# ---------- Vista ----------
def barras_h(serie, titulo, etiqueta):
    d = serie.reset_index()
    d.columns = ["Item", etiqueta]
    fig = px.bar(d.sort_values(etiqueta), x=etiqueta, y="Item", orientation="h", title=titulo)
    fig.update_layout(
        template="plotly_dark", height=320, xaxis_title=None, yaxis_title=None,
        margin=dict(l=0, r=0, t=40, b=0),
    )
    st.plotly_chart(fig, use_container_width=True)


def tab_reporte():
    if not acceso("acceso_medidas", "MEDIDAS_PASSWORD", "Reporte de inspecciones (SST y RR. HH.)"):
        return

    st.subheader("Reporte de inspecciones")
    insp = inspeccion.cargar_inspecciones()
    if insp.empty:
        st.info("Todavía no hay inspecciones registradas.")
        return

    insp = insp.assign(
        fecha=pd.to_datetime(insp["fecha_hora"], utc=True).dt.tz_convert("America/Bogota").dt.date,
        Fecha=insp["fecha_hora"].map(fecha_local),
        area=insp["area"].fillna("Sin área"),
        cumple=insp["cumple"].astype(bool),
    )

    c1, c2 = st.columns(2)
    min_f, max_f = insp["fecha"].min(), insp["fecha"].max()
    rango = c1.date_input(
        "Período",
        value=(max(min_f, max_f - timedelta(days=90)), max_f),
        min_value=min_f, max_value=max_f, key="rep_rango",
    )
    area = c2.selectbox("Área", ["Todas"] + sorted(insp["area"].unique()), key="rep_area")
    if not (isinstance(rango, (tuple, list)) and len(rango) == 2):
        st.info("Selecciona la fecha inicial y la final.")
        return

    v = insp[(insp["fecha"] >= rango[0]) & (insp["fecha"] <= rango[1])]
    if area != "Todas":
        v = v[v["area"] == area]
    v = v.copy()
    if v.empty:
        st.warning("No hay inspecciones con esos filtros.")
        return

    ids = set(v["inspeccion_id"])
    items = cargar_items()
    causas = cargar_causas()
    it = items[items["inspeccion_id"].isin(ids)] if not items.empty else items
    ca = causas[causas["inspeccion_id"].isin(ids)] if not causas.empty else causas

    # --- Indicadores ---
    total = len(v)
    cumplen = int(v["cumple"].sum())
    pendientes = int(((~v["cumple"]) & (v["n_medidas"] == 0)).sum())
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Inspecciones", f"{total:,}")
    k2.metric("Personas inspeccionadas", f"{v['trabajador_id'].nunique():,}")
    k3.metric("Cumplimiento", f"{cumplen / total * 100:.1f}%")
    k4.metric("Pendientes de medida", f"{pendientes:,}")
    st.caption("Cumplimiento = inspecciones sin ningún ítem en NC.")

    g1, g2 = st.columns(2)
    with g1:
        if not it.empty and (it["estado"] == "NC").any():
            nc = it[it["estado"] == "NC"].groupby("item").size().sort_values(ascending=False).head(10)
            barras_h(nc, "Ítems más incumplidos", "Veces en NC")
        else:
            st.info("Sin ítems en NC en el período.")
    with g2:
        if not ca.empty:
            ca = ca.assign(etiqueta=ca["codigo"].astype(str) + ". " + ca["texto"].str.slice(0, 38))
            frec = ca.groupby("etiqueta").size().sort_values(ascending=False).head(8)
            barras_h(frec, "Causas más frecuentes", "Veces")
        else:
            st.info("Sin causas registradas en el período.")

    if area == "Todas" and v["area"].nunique() > 1:
        por_area = v.groupby("area")["cumple"].mean().mul(100).round(1).sort_values(ascending=False)
        barras_h(por_area, "Cumplimiento por área (%)", "% de inspecciones sin NC")

    # --- Tabla y Excel ---
    st.markdown("### Inspecciones")
    tabla = v[[
        "inspeccion_id", "Fecha", "trabajador", "cedula", "area", "cumple",
        "items_incumplidos", "n_medidas",
    ]].rename(columns={
        "inspeccion_id": "Inspección", "trabajador": "Trabajador", "cedula": "Cédula",
        "area": "Área", "cumple": "Cumple", "items_incumplidos": "Ítems incumplidos",
        "n_medidas": "Medidas asignadas",
    })
    st.dataframe(tabla, hide_index=True, use_container_width=True)

    hojas = {"Formato": hoja_formato(v, it, ca if "etiqueta" not in ca.columns else ca.drop(columns="etiqueta"))}
    if not it.empty:
        hojas["Detalle de ítems"] = it[["inspeccion_id", "trabajador", "cedula", "area", "tipo", "item", "estado"]]
    st.download_button(
        "⬇️ Descargar inspecciones en Excel",
        data=a_excel(hojas),
        file_name=f"Inspecciones_EPP_{date.today():%Y%m%d}.xlsx",
        mime=XLSX_MIME,
        type="primary",
    )
    st.caption(
        "La hoja «Formato» tiene una fila por persona y una columna por ítem, "
        "como el formato físico. Contiene información sensible: manéjala con cuidado."
    )

    # --- PDF de una inspección ---
    st.markdown("### PDF de una inspección")
    etiquetas = {
        int(r.inspeccion_id): f"#{int(r.inspeccion_id)} · {r.trabajador} · {r.Fecha}"
        for r in v.sort_values("inspeccion_id", ascending=False).itertuples()
    }
    elegida = st.selectbox(
        "Selecciona una inspección", list(etiquetas), format_func=lambda i: etiquetas[i], key="rep_pdf"
    )
    try:
        pdf_bytes, firma = pdf_inspeccion(elegida)
    except Exception as ex:
        st.warning(f"No se pudo generar el PDF: {ex}")
        return
    col_img, col_pdf = st.columns(2)
    with col_img:
        if firma:
            st.image(firma, width=240, caption="Firma registrada")
    with col_pdf:
        st.download_button(
            "📄 Descargar PDF de la inspección",
            data=pdf_bytes,
            file_name=f"Inspeccion_EPP_{elegida}.pdf",
            mime="application/pdf",
            type="primary",
            use_container_width=True,
        )
