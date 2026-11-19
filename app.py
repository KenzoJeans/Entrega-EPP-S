import hmac
import io
import os
import re
import uuid
from datetime import datetime

import pandas as pd
import streamlit as st
from PIL import Image
from streamlit_drawable_canvas import st_canvas
from supabase import create_client

import urllib.request
from datetime import date, timedelta
from fpdf import FPDF

st.set_page_config(page_title="Entrega de EPP", page_icon="🦺", layout="centered")

MOTIVOS = ["NUEVO", "REPOSICION", "DETERIORO"]
LOGO = "logo.png"
LOGO_NEGRO = "logonegro.png"
COLUMNAS = 3  # tarjetas por fila: prueba 2, 3 o 4
BUCKET_FIRMAS = "firmas-epp"

MOTIVOS = ["NUEVO", "REPOSICION", "DETERIORO"]
ROLES_ENTREGA = [
    "Brigadista",
    "Líder de SST",
    "Auxiliar de SST",
    "Enfermera",
    "Líder de Gestión Ambiental",
    "Analista de Gestión Ambiental",
]


# ---------- Conexión ----------
@st.cache_resource
def get_sb():
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_SERVICE_KEY"])


# ---------- Acceso con contraseña ----------
def encabezado():
    if os.path.exists(LOGO):
        st.image(LOGO, width=140)
    st.title("Entrega de EPP")


def check_password():
    if st.session_state.get("auth_ok"):
        return True
    encabezado()
    pwd = st.text_input("Contraseña", type="password")
    if st.button("Ingresar", type="primary"):
        if hmac.compare_digest(pwd, st.secrets["APP_PASSWORD"]):
            st.session_state["auth_ok"] = True
            st.rerun()
        else:
            st.error("Contraseña incorrecta")
    return False


# ---------- Datos ----------
@st.cache_data(ttl=300)
def cargar_catalogo():
    r = (
        get_sb().table("epp_catalogo")
        .select("id,nombre,categoria,requiere_talla,imagen_url")
        .eq("activo", True)
        .order("nombre")
        .execute()
    )
    return r.data


def buscar_trabajadores(texto):
    texto = re.sub(r"[,()%*]", " ", texto).strip()
    if len(texto) < 3:
        return []
    r = (
        get_sb().table("trabajadores")
        .select("id,cedula,nombre,cargo,sucursal")
        .eq("estado_laboral", "ACTIVO")
        .or_(f"cedula.ilike.%{texto}%,nombre.ilike.%{texto}%")
        .order("nombre")
        .limit(20)
        .execute()
    )
    return r.data


def firma_a_png(image_data):
    img = Image.fromarray(image_data.astype("uint8"), "RGBA")
    fondo = Image.new("RGB", img.size, (255, 255, 255))
    fondo.paste(img, mask=img.split()[3])
    buf = io.BytesIO()
    fondo.save(buf, format="PNG")
    return buf.getvalue()


# ---------- Selector de EPP con imágenes ----------
def css_grid():
    ancho = f"calc({100 / COLUMNAS:.2f}% - 0.6rem)"
    return f"""
    <style>
    .st-key-grid_epp [data-testid="stHorizontalBlock"] {{ flex-wrap: wrap; gap: 0.5rem; }}
    .st-key-grid_epp [data-testid="stColumn"],
    .st-key-grid_epp [data-testid="column"] {{
        min-width: {ancho} !important;
        flex: 1 1 {ancho} !important;
    }}
    .st-key-grid_epp button {{ min-height: 2.8rem; height: auto; padding: 0.25rem 0.4rem; }}
    .st-key-grid_epp button p {{ white-space: normal; line-height: 1.15; font-size: 0.8rem; }}
    </style>
    """


def imagen_html(url):
    estilo = (
        "width:100%;max-width:160px;aspect-ratio:1/1;margin:0 auto 0.25rem auto;"
        "border-radius:10px;display:block;"
    )
    if url:
        return f'<img src="{url}" style="{estilo}object-fit:contain;background:#fff;">'
    return (
        f'<div style="{estilo}display:flex;align-items:center;justify-content:center;'
        'background:#1A1F2B;font-size:2.2rem;">🦺</div>'
    )


def alternar(clave, epp_id):
    sel = st.session_state[clave]
    if epp_id in sel:
        sel.remove(epp_id)
    else:
        sel.append(epp_id)


def selector_epp(catalogo, n):
    clave = f"sel{n}"
    st.session_state.setdefault(clave, [])
    seleccion = st.session_state[clave]
    st.markdown(css_grid(), unsafe_allow_html=True)

    categorias = ["Todas"] + sorted({e.get("categoria") or "Otros" for e in catalogo.values()})
    cat = st.pills("Categoría", categorias, default="Todas", key=f"cat{n}") or "Todas"
    buscar = st.text_input("Buscar EPP", key=f"bq{n}", placeholder="Ej: guantes, filtro...")

    if seleccion:
        nombres = [catalogo[i]["nombre"] for i in seleccion if i in catalogo]
        st.caption("Seleccionados: " + ", ".join(nombres))

    lista = [
        e for e in catalogo.values()
        if (cat == "Todas" or (e.get("categoria") or "Otros") == cat)
        and buscar.strip().lower() in e["nombre"].lower()
    ]
    if not lista:
        st.info("No hay EPP con ese filtro.")

    with st.container(key="grid_epp"):
        for k in range(0, len(lista), COLUMNAS):
            cols = st.columns(COLUMNAS)
            for col, e in zip(cols, lista[k:k + COLUMNAS]):
                with col:
                    st.markdown(imagen_html(e.get("imagen_url")), unsafe_allow_html=True)
                    marcado = e["id"] in seleccion
                    st.button(
                        ("✓ " if marcado else "") + e["nombre"],
                        key=f"b{e['id']}_{n}",
                        type="primary" if marcado else "secondary",
                        use_container_width=True,
                        on_click=alternar,
                        args=(clave, e["id"]),
                    )
    return seleccion


# ---------- Pestaña 1: registrar ----------
def tab_registro():
    n = st.session_state.setdefault("form_n", 0)  # cambia para limpiar el formulario

    msg = st.session_state.pop("ok_msg", None)
    if msg:
        st.success(msg)

    # 1. Trabajador
    st.subheader("1. Datos del trabajador")
    q = st.text_input("Buscar por cédula o nombre", key=f"q{n}")
    trabajador = None
    if q and len(q.strip()) < 3:
        st.caption("Escribe al menos 3 caracteres.")
    elif q:
        resultados = buscar_trabajadores(q)
        if not resultados:
            st.warning("No se encontró ningún trabajador activo con ese dato.")
        else:
            por_id = {t["id"]: t for t in resultados}
            tid = st.selectbox(
                "Selecciona al trabajador",
                list(por_id),
                format_func=lambda i: f"{por_id[i]['nombre']} · {por_id[i]['cedula']}",
                key=f"t{n}",
            )
            trabajador = por_id[tid]
            st.caption(f"Cargo: {trabajador['cargo']} · Sucursal: {trabajador['sucursal']}")

    # 2. EPPs
    st.subheader("2. Lista de EPP entregados")
    catalogo = {e["id"]: e for e in cargar_catalogo()}
    elegidos = selector_epp(catalogo, n)
    items = []
    for i in elegidos:
        e = catalogo.get(i)
        if e is None:
            continue
        st.markdown(f"**{e['nombre']}**")
        c1, c2, c3 = st.columns(3)
        cantidad = c1.number_input("Cantidad", 1, 50, 1, key=f"c{i}_{n}")
        if e["requiere_talla"]:
            talla = c2.text_input("Talla", key=f"tl{i}_{n}").strip() or None
        else:
            talla = None
            c2.caption("Sin talla")
        motivo = c3.selectbox("Motivo", MOTIVOS, key=f"m{i}_{n}")
        items.append({
            "epp_id": i, "cantidad": int(cantidad), "talla": talla, "motivo": motivo,
            "_requiere_talla": e["requiere_talla"], "_nombre": e["nombre"],
        })

    # 3. Cierre
    st.subheader("3. Cierre")
    entregado_por = st.selectbox(
        "Entregado por",
        options=ROLES_ENTREGA,
        index=None,
        placeholder="Selecciona quién realiza la entrega...",
        key=f"ep{n}",
    )
    observaciones = st.text_area("Observaciones (opcional)", key=f"ob{n}")

    # 4. Firma
    st.subheader("4. Firma del trabajador")
    st.caption("El trabajador firma con el dedo dentro del recuadro.")
    lienzo = st_canvas(
        stroke_width=3,
        stroke_color="#000000",
        background_color="#FFFFFF",
        height=160,
        width=320,
        drawing_mode="freedraw",
        display_toolbar=True,
        key=f"firma{n}",
    )
    firmado = bool(lienzo.json_data and lienzo.json_data.get("objects"))

    if st.button("Guardar entrega", type="primary", use_container_width=True):
        errores = []
        if trabajador is None:
            errores.append("Selecciona un trabajador.")
        if not items:
            errores.append("Selecciona al menos un EPP.")
        if not entregado_por:
            errores.append("Selecciona quién entrega.")
        for it in items:
            if it["_requiere_talla"] and not it["talla"]:
                errores.append(f"Falta la talla de {it['_nombre']}.")
        if not firmado:
            errores.append("Falta la firma del trabajador.")

        if errores:
            for err in errores:
                st.error(err)
            return

        payload = [{k: v for k, v in it.items() if not k.startswith("_")} for it in items]
        png = firma_a_png(lienzo.image_data)
        ruta = f"{datetime.now():%Y/%m}/{uuid.uuid4().hex}.png"
        subida = False
        guardado = False
        try:
            get_sb().storage.from_(BUCKET_FIRMAS).upload(
                ruta, png, {"content-type": "image/png"}
            )
            subida = True
            r = get_sb().rpc("registrar_entrega", {
                "p_trabajador_id": trabajador["id"],
                "p_entregado_por": entregado_por.strip(),
                "p_observaciones": observaciones.strip() or None,
                "p_items": payload,
                "p_firma_path": ruta,
            }).execute()
            st.session_state["ok_msg"] = (
                f"✅ Entrega #{r.data} registrada para {trabajador['nombre']}."
            )
            guardado = True
        except Exception as ex:
            st.error(f"No se pudo guardar: {ex}")
            if subida and not guardado:
                try:  # evita dejar una firma huérfana en el bucket
                    get_sb().storage.from_(BUCKET_FIRMAS).remove([ruta])
                except Exception:
                    pass

        if guardado:
            st.session_state["form_n"] = n + 1
            st.rerun()


# ---------- Pestaña 2: historial ----------
def tab_historial():
    st.subheader("Últimas 50 entregas")
    r = (
        get_sb().table("entregas")
        .select(
            "id,fecha_hora,entregado_por,firma_path,"
            "trabajadores(nombre,cedula),"
            "entrega_detalle(cantidad,talla,motivo,epp_catalogo(nombre))"
        )
        .order("fecha_hora", desc=True)
        .limit(50)
        .execute()
    )
    filas = []
    firmas = {}
    for e in r.data:
        firmas[e["id"]] = e.get("firma_path")
        fecha = (
            pd.to_datetime(e["fecha_hora"], utc=True)
            .tz_convert("America/Bogota")
            .strftime("%Y-%m-%d %H:%M")
        )
        for d in e["entrega_detalle"]:
            filas.append({
                "Entrega": e["id"],
                "Fecha": fecha,
                "Trabajador": e["trabajadores"]["nombre"],
                "Cédula": e["trabajadores"]["cedula"],
                "EPP": d["epp_catalogo"]["nombre"],
                "Cant.": d["cantidad"],
                "Talla": d["talla"],
                "Motivo": d["motivo"],
                "Entregó": e["entregado_por"],
            })

    if not filas:
        st.info("Todavía no hay entregas registradas.")
        return

    st.dataframe(pd.DataFrame(filas), use_container_width=True, hide_index=True)

    st.markdown("**Ver firma de una entrega**")
    elegida = st.selectbox("Entrega", list(firmas), format_func=lambda i: f"Entrega #{i}")
    ruta = firmas.get(elegida)
    if ruta:
        try:
            res = get_sb().storage.from_(BUCKET_FIRMAS).create_signed_url(ruta, 300)
            url = res.get("signedURL") or res.get("signedUrl")
            st.image(url, width=260)
        except Exception as ex:
            st.warning(f"No se pudo cargar la firma: {ex}")
    else:
        st.caption("Esta entrega no tiene firma.")

# ---------- Generador de Acta PDF con Firma ----------
from fpdf import FPDF


def _txt(texto):
    """Asegura compatibilidad de tildes y caracteres latinos en FPDF."""
    return str(texto or "").encode("latin-1", "replace").decode("latin-1")


class ActaEPP(FPDF):
    def header(self):
        # Marco del encabezado estilo Sistema de Gestión (3 columnas)
        y_inicio = 10
        alto_hdr = 24
        self.set_line_width(0.3)

        # Columna 1: Logo (x=10 a 50)
        self.rect(10, y_inicio, 40, alto_hdr)
        if os.path.exists(LOGO_NEGRO):
            self.image(LOGO_NEGRO, x=13, y=y_inicio, w=34)

        # Columna 2: Razón Social, Título y Datos Corporativos (x=50 a 152)
        self.rect(50, y_inicio, 102, alto_hdr)
        self.set_xy(50, y_inicio + 2)
        self.set_font("Helvetica", "B", 10)
        self.cell(102, 4.5, _txt("KENZO JEANS S.A.S."), align="C", ln=True)

        self.set_x(50)
        self.set_font("Helvetica", "B", 8.5)
        self.cell(
            102,
            4.5,
            _txt("ACTA DE ENTREGA DE ELEMENTOS DE PROTECCIÓN PERSONAL"),
            align="C",
            ln=True,
        )

        self.set_x(50)
        self.set_font("Helvetica", "", 7)
        self.cell(
            102,
            3.8,
            _txt("Sociedad por Acciones Simplificada · Bogotá D.C., Colombia"),
            align="C",
            ln=True,
        )
        self.set_x(50)
        self.cell(
            102,
            3.8,
            _txt("Dirección: Carrera 68D # 11-71, Bogotá · Tel: (601) 786 8232"),
            align="C",
            ln=True,
        )

        # Columna 3: Control Documental SST (x=152 a 200)
        self.rect(152, y_inicio, 48, 8)
        self.rect(152, y_inicio + 8, 48, 8)
        self.rect(152, y_inicio + 16, 48, 8)

        self.set_font("Helvetica", "B", 7.5)
        self.set_xy(153, y_inicio + 1.5)
        self.cell(46, 5, _txt("Código: SST-HI-ED-FO-04"), align="L")

        self.set_xy(153, y_inicio + 9.5)
        self.cell(46, 5, _txt("Versión: 2"), align="L")

        self.set_xy(153, y_inicio + 17.5)
        self.set_font("Helvetica", "", 7.5)
        self.cell(46, 5, _txt(f"Página {self.page_no()} de {{nb}}"), align="L")

        self.set_y(y_inicio + alto_hdr + 5)

    def footer(self):
        self.set_y(-12)
        self.set_font("Helvetica", "I", 7)
        self.set_text_color(110, 110, 110)
        self.cell(
            0,
            5,
            _txt("KENZO JEANS S.A.S. - Sistema de Gestión de Seguridad y Salud en el Trabajo (SG-SST)"),
            align="C",
        )
        self.set_text_color(0, 0, 0)


def generar_acta_pdf(entrega, firma_bytes=None):
    pdf = ActaEPP()
    pdf.alias_nb_pages()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=16)

    # Sub-barra con consecutivo y fecha de entrega
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(95, 6, _txt(f"ACTA DE ENTREGA N.° {entrega['id']}"), align="L")
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(95, 6, _txt(f"Fecha y hora: {entrega['fecha_local']}"), align="R", ln=True)
    pdf.ln(2)

    # 1. Datos del trabajador
    trab = entrega["trabajadores"]
    pdf.set_fill_color(235, 238, 242)
    pdf.set_font("Helvetica", "B", 9.5)
    pdf.cell(0, 7, _txt(" 1. DATOS DEL TRABAJADOR"), border=1, ln=True, fill=True)

    pdf.set_font("Helvetica", "", 9)
    pdf.cell(110, 7, _txt(f" Nombre: {trab['nombre']}"), border=1)
    pdf.cell(80, 7, _txt(f" Cédula: {trab['cedula']}"), border=1, ln=True)
    pdf.cell(110, 7, _txt(f" Cargo: {trab.get('cargo') or 'N/A'}"), border=1)
    pdf.cell(80, 7, _txt(f" Sucursal: {trab.get('sucursal') or 'N/A'}"), border=1, ln=True)
    pdf.ln(5)

    # 2. Tabla de EPPs entregados
    pdf.set_font("Helvetica", "B", 9.5)
    pdf.cell(0, 7, _txt(" 2. ELEMENTOS DE PROTECCIÓN PERSONAL ENTREGADOS"), border=1, ln=True, fill=True)

    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(95, 7, _txt(" Elemento (EPP)"), border=1)
    pdf.cell(25, 7, _txt("Cantidad"), border=1, align="C")
    pdf.cell(30, 7, _txt("Talla"), border=1, align="C")
    pdf.cell(40, 7, _txt("Motivo"), border=1, align="C", ln=True)

    pdf.set_font("Helvetica", "", 9)
    for d in entrega["entrega_detalle"]:
        nombre_epp = d["epp_catalogo"]["nombre"]
        talla = d["talla"] or "-"
        pdf.cell(95, 7, _txt(f" {nombre_epp[:48]}"), border=1)
        pdf.cell(25, 7, str(d["cantidad"]), border=1, align="C")
        pdf.cell(30, 7, _txt(talla), border=1, align="C")
        pdf.cell(40, 7, _txt(d["motivo"]), border=1, align="C", ln=True)
    pdf.ln(4)

    # Observaciones (si existen)
    if entrega.get("observaciones"):
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(0, 6, _txt(" Observaciones:"), border="LTR", ln=True, fill=True)
        pdf.set_font("Helvetica", "", 8.5)
        pdf.multi_cell(0, 5.5, _txt(f" {entrega['observaciones']}"), border="LBR")
        pdf.ln(4)

    # 3. Compromiso y Responsabilidades del Trabajador
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(0, 6.5, _txt(" 3. COMPROMISO Y RESPONSABILIDADES DEL TRABAJADOR"), border=1, ln=True, fill=True)

    texto_legal = (
        "1. COMPROMISO DEL TRABAJADOR: Me comprometo a utilizar adecuadamente durante la jornada laboral "
        "los elementos de protección personal, recibirlos y mantenerlos en buen estado, dando cumplimiento a las "
        "políticas u responsabilidades que tengo con mi seguridad y salud en mi trabajo.\n"
        "2. RESPONSABILIDADES DEL TRABAJADOR: Usando los equipos de protección personal estoy cumpliendo con mis "
        "deberes como trabajador definidos en la ley a través de la siguiente normatividad: CODIGO SUSTANTIVO DEL "
        "TRABAJO; Art. 56 y Art. 58, numeral 7; LEY 9 DE 1979; Art. 88."
    )
    pdf.set_font("Helvetica", "", 8)
    pdf.multi_cell(0, 4.5, _txt(texto_legal), border=1, align="J")
    pdf.ln(6)

    # Bloque de firmas (si queda poco espacio al final de la página, salta limpiamente)
    if pdf.get_y() > 235:
        pdf.add_page()

    y_firma = pdf.get_y()
    if firma_bytes:
        img_stream = io.BytesIO(firma_bytes)
        pdf.image(img_stream, x=28, y=y_firma, w=55, h=24)

    pdf.set_y(y_firma + 25)
    pdf.set_font("Helvetica", "B", 8.5)
    pdf.cell(95, 4.5, "________________________________________", align="C")
    pdf.cell(95, 4.5, "________________________________________", align="C", ln=True)
    pdf.cell(95, 4.5, _txt("FIRMA DEL TRABAJADOR"), align="C")
    pdf.cell(95, 4.5, _txt("ENTREGADO POR"), align="C", ln=True)

    pdf.set_font("Helvetica", "", 8)
    pdf.cell(95, 4.5, _txt(f"{trab['nombre']} · C.C. {trab['cedula']}"), align="C")
    pdf.cell(95, 4.5, _txt(entrega["entregado_por"]), align="C", ln=True)

    return bytes(pdf.output())

# ---------- Pestaña 2: historial + descarga PDF ----------
def tab_historial():
    st.subheader("Últimas 50 entregas")
    r = (
        get_sb().table("entregas")
        .select(
            "id,fecha_hora,entregado_por,observaciones,firma_path,"
            "trabajadores(nombre,cedula,cargo,sucursal),"
            "entrega_detalle(cantidad,talla,motivo,epp_catalogo(nombre))"
        )
        .order("fecha_hora", desc=True)
        .limit(50)
        .execute()
    )
    if not r.data:
        st.info("Todavía no hay entregas registradas.")
        return

    filas = []
    entregas_map = {}
    for e in r.data:
        fecha = (
            pd.to_datetime(e["fecha_hora"], utc=True)
            .tz_convert("America/Bogota")
            .strftime("%Y-%m-%d %H:%M")
        )
        e["fecha_local"] = fecha
        entregas_map[e["id"]] = e
        for d in e["entrega_detalle"]:
            filas.append({
                "Entrega": e["id"],
                "Fecha": fecha,
                "Trabajador": e["trabajadores"]["nombre"],
                "Cédula": e["trabajadores"]["cedula"],
                "EPP": d["epp_catalogo"]["nombre"],
                "Cant.": d["cantidad"],
                "Talla": d["talla"],
                "Motivo": d["motivo"],
                "Entregó": e["entregado_por"],
            })

    st.dataframe(pd.DataFrame(filas), use_container_width=True, hide_index=True)

    st.markdown("---")
    st.markdown("**Detalle y descarga de acta firmada**")
    elegida = st.selectbox(
        "Selecciona una entrega",
        list(entregas_map),
        format_func=lambda i: f"Entrega #{i} · {entregas_map[i]['trabajadores']['nombre']} ({entregas_map[i]['fecha_local']})",
    )
    entrega_sel = entregas_map[elegida]
    ruta = entrega_sel.get("firma_path")

    col_img, col_pdf = st.columns([1, 1])
    firma_bytes = None
    with col_img:
        if ruta:
            try:
                firma_bytes = get_sb().storage.from_(BUCKET_FIRMAS).download(ruta)
                st.image(firma_bytes, width=240, caption="Firma registrada")
            except Exception as ex:
                st.warning(f"No se pudo cargar la firma: {ex}")
        else:
            st.caption("Esta entrega no tiene firma.")

    with col_pdf:
        pdf_bytes = generar_acta_pdf(entrega_sel, firma_bytes)
        st.download_button(
            label="📄 Descargar Acta en PDF",
            data=pdf_bytes,
            file_name=f"Acta_EPP_{entrega_sel['id']}_{entrega_sel['trabajadores']['cedula']}.pdf",
            mime="application/pdf",
            type="primary",
            use_container_width=True,
        )


# ---------- Pestaña 3: KPIs e Indicadores ----------
@st.cache_data(ttl=120)
def cargar_datos_kpi():
    r = (
        get_sb().table("entregas")
        .select(
            "id,fecha_hora,entregado_por,"
            "trabajadores(nombre,cedula,cargo,sucursal),"
            "entrega_detalle(cantidad,talla,motivo,epp_catalogo(nombre,categoria))"
        )
        .order("fecha_hora", desc=True)
        .execute()
    )
    registros = []
    for e in r.data:
        dt = pd.to_datetime(e["fecha_hora"], utc=True).tz_convert("America/Bogota")
        trab = e.get("trabajadores") or {}
        for d in e.get("entrega_detalle") or []:
            epp = d.get("epp_catalogo") or {}
            registros.append({
                "entrega_id": e["id"],
                "fecha": dt.date(),
                "mes": dt.strftime("%Y-%m"),
                "trabajador": trab.get("nombre", "N/A"),
                "cedula": trab.get("cedula", "N/A"),
                "cargo": trab.get("cargo") or "Sin cargo",
                "sucursal": trab.get("sucursal") or "Sin sucursal",
                "epp": epp.get("nombre", "N/A"),
                "categoria": epp.get("categoria") or "Otros",
                "cantidad": d["cantidad"],
                "motivo": d["motivo"],
            })
    return pd.DataFrame(registros)


def tab_kpis():
    st.subheader("Indicadores de Gestión de EPP")
    df = cargar_datos_kpi()
    if df.empty:
        st.info("No hay datos suficientes para calcular indicadores.")
        return

    # Filtros superiores
    c1, c2, c3 = st.columns(3)
    min_f, max_f = df["fecha"].min(), df["fecha"].max()
    rango = c1.date_input("Rango de fechas", value=(min_f, max_f), min_value=min_f, max_value=max_f)
    sucursales = ["Todas"] + sorted(df["sucursal"].unique().tolist())
    suc_sel = c2.selectbox("Sucursal", sucursales)
    motivos_sel = c3.multiselect("Motivo", MOTIVOS, default=MOTIVOS)

    if isinstance(rango, tuple) and len(rango) == 2:
        df = df[(df["fecha"] >= rango[0]) & (df["fecha"] <= rango[1])]
    if suc_sel != "Todas":
        df = df[df["sucursal"] == suc_sel]
    if motivos_sel:
        df = df[df["motivo"].isin(motivos_sel)]

    if df.empty:
        st.warning("No hay registros con los filtros seleccionados.")
        return

    # KPIs principales
    total_unidades = int(df["cantidad"].sum())
    total_actas = df["entrega_id"].nunique()
    trabajadores_unicos = df["cedula"].nunique()
    unid_deterioro = int(df.loc[df["motivo"] == "DETERIORO", "cantidad"].sum())
    tasa_deterioro = (unid_deterioro / total_unidades * 100) if total_unidades else 0

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("EPPs entregados", f"{total_unidades:,}")
    k2.metric("Actas firmadas", f"{total_actas:,}")
    k3.metric("Trabajadores dotados", f"{trabajadores_unicos:,}")
    k4.metric("% por Deterioro", f"{tasa_deterioro:.1f}%")

    st.markdown("---")

    # Gráficos
    g1, g2 = st.columns(2)
    with g1:
        st.markdown("**Top 10 EPPs más entregados**")
        top_epp = df.groupby("epp")["cantidad"].sum().sort_values(ascending=False).head(10)
        st.bar_chart(top_epp, horizontal=True)

    with g2:
        st.markdown("**Entregas por Motivo**")
        por_motivo = df.groupby("motivo")["cantidad"].sum()
        st.bar_chart(por_motivo)

    g3, g4 = st.columns(2)
    with g3:
        st.markdown("**Consumo por Sucursal**")
        por_suc = df.groupby("sucursal")["cantidad"].sum().sort_values(ascending=False)
        st.bar_chart(por_suc)

    with g4:
        st.markdown("**Evolución Mensual (Unidades)**")
        por_mes = df.groupby("mes")["cantidad"].sum().sort_index()
        st.line_chart(por_mes)


# ---------- Programa principal ----------
if not check_password():
    st.stop()

encabezado()
t1, t2, t3 = st.tabs(["Registrar entrega", "Historial y Actas", "Indicadores (KPIs)"])
with t1:
    tab_registro()
with t2:
    tab_historial()
with t3:
    tab_kpis()
