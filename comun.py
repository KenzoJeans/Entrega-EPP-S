"""Funciones compartidas por todas las vistas de la app."""
import hmac
import io
import unicodedata

import pandas as pd
import streamlit as st
from PIL import Image
from supabase import create_client

BUCKET_FIRMAS = "firmas-epp"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


# ---------- Conexión ----------
@st.cache_resource
def get_sb():
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_SERVICE_KEY"])


def traer_todo(fabrica, tam=1000):
    """Pide los datos por páginas para superar el tope de 1000 filas."""
    filas, desde = [], 0
    while True:
        r = fabrica().range(desde, desde + tam - 1).execute()
        filas += r.data
        if len(r.data) < tam:
            break
        desde += tam
    return filas


# ---------- Texto y fechas ----------
def normalizar(texto):
    sin_tildes = unicodedata.normalize("NFKD", str(texto)).encode("ascii", "ignore").decode()
    return sin_tildes.lower()


def fecha_local(valor):
    """Convierte una fecha UTC de la base de datos a hora de Bogotá (texto)."""
    return (
        pd.to_datetime(valor, utc=True)
        .tz_convert("America/Bogota")
        .strftime("%Y-%m-%d %H:%M")
    )


# ---------- Trabajadores ----------
@st.cache_data(ttl=600, show_spinner=False)
def cargar_activos():
    filas, desde = [], 0
    while True:
        r = (
            get_sb().table("trabajadores")
            .select("id,cedula,nombre,cargo,sucursal,centros_costo(nombre)")
            .eq("estado_laboral", "ACTIVO")
            .order("id")
            .range(desde, desde + 999)
            .execute()
        )
        filas += r.data
        if len(r.data) < 1000:
            break
        desde += 1000
    for w in filas:
        cc = w.pop("centros_costo", None) or {}
        w["area"] = cc.get("nombre") or "Sin área"
        w["_busq"] = normalizar(f"{w['nombre']} {w['cedula']}")
    return filas


def buscar_trabajadores(texto):
    t = normalizar(texto).strip()
    if len(t) < 3:
        return []
    return [w for w in cargar_activos() if t in w["_busq"]][:20]


# ---------- Firma ----------
def firma_a_png(image_data):
    img = Image.fromarray(image_data.astype("uint8"), "RGBA")
    fondo = Image.new("RGB", img.size, (255, 255, 255))
    fondo.paste(img, mask=img.split()[3])
    buf = io.BytesIO()
    fondo.save(buf, format="PNG")
    return buf.getvalue()


# ---------- Acceso con clave propia (vistas sensibles) ----------
def acceso(clave_estado, secreto, titulo):
    """Pide una clave distinta a la general. Si el secreto no existe, bloquea."""
    if st.session_state.get(clave_estado):
        return True
    st.subheader(titulo)
    esperado = st.secrets.get(secreto)
    if not esperado:
        st.error(
            f"Falta configurar `{secreto}` en los Secrets de la app. "
            "Por seguridad, esta vista permanece bloqueada."
        )
        return False
    st.info("Esta vista maneja información sensible y requiere una clave propia.")
    clave = st.text_input("Clave de acceso", type="password", key=f"{clave_estado}_clave")
    if st.button("Entrar", key=f"{clave_estado}_entrar"):
        if hmac.compare_digest(clave, str(esperado)):
            st.session_state[clave_estado] = True
            st.rerun()
        else:
            st.error("Clave incorrecta.")
    return False


# ---------- Excel ----------
def a_excel(hojas):
    """Convierte {nombre_hoja: DataFrame} en un .xlsx con formato."""
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as xl:
        encabezado_fmt = xl.book.add_format({
            "bold": True, "bg_color": "#1F7A3D", "font_color": "#FFFFFF",
            "border": 1, "valign": "vcenter",
        })
        for nombre, df in hojas.items():
            hoja = nombre[:31]
            df.to_excel(xl, sheet_name=hoja, index=False)
            ws = xl.sheets[hoja]
            for j, col in enumerate(df.columns):
                ws.write(0, j, col, encabezado_fmt)
                largo = df[col].astype(str).str.len().max() if len(df) else 0
                ws.set_column(j, j, min(max(len(str(col)), largo) + 2, 45))
            ws.freeze_panes(1, 0)
            if len(df):
                ws.autofilter(0, 0, len(df), len(df.columns) - 1)
    return buf.getvalue()
