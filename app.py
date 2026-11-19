import hmac
import io
import os
import unicodedata
import uuid
from datetime import date, datetime, timedelta

import pandas as pd
import plotly.express as px
import streamlit as st
from fpdf import FPDF
from PIL import Image
from streamlit_drawable_canvas import st_canvas
from supabase import create_client

st.set_page_config(page_title="Entrega de EPP", page_icon="🦺", layout="centered")

LOGO = "logo.png"
LOGO_NEGRO = "logonegro.png"
COLUMNAS = 3  # tarjetas por fila: prueba 2, 3 o 4
BUCKET_FIRMAS = "firmas-epp"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# Datos del formato oficial (cámbialos aquí cuando SST actualice el formato)
CODIGO_FORMATO = "SST-HI-ED-FO-04"
VERSION_FORMATO = "2"

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


def normalizar(texto):
    sin_tildes = unicodedata.normalize("NFKD", str(texto)).encode("ascii", "ignore").decode()
    return sin_tildes.lower()


@st.cache_data(ttl=600, show_spinner=False)
def cargar_activos():
    filas, desde = [], 0
    while True:
        r = (
            get_sb().table("trabajadores")
            .select("id,cedula,nombre,cargo,sucursal")
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
        w["_busq"] = normalizar(f"{w['nombre']} {w['cedula']}")
    return filas


def buscar_trabajadores(texto):
    t = normalizar(texto).strip()
    if len(t) < 3:
        return []
    return [w for w in cargar_activos() if t in w["_busq"]][:20]




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
        with st.spinner("Guardando entrega..."):
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
            cargar_detalle.clear()
            cargar_cobertura.clear()
            cargar_reposicion.clear()
            st.session_state["form_n"] = n + 1
            st.rerun()


# ---------- Generador de Acta PDF con Firma ----------


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
            self.image(LOGO_NEGRO, x=13, y=y_inicio, w=24)

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
        self.cell(46, 5, _txt(f"Código: {CODIGO_FORMATO}"), align="L")

        self.set_xy(153, y_inicio + 9.5)
        self.cell(46, 5, _txt(f"Versión: {VERSION_FORMATO}"), align="L")

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
        "políticas y responsabilidades que tengo con mi seguridad y salud en mi trabajo.\n"
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
        pdf.image(img_stream, x=33, y=y_firma, w=48, h=24)

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


# ---------- Datos para historial y KPIs ----------
MAPA_DETALLE = {
    "entrega_id": "Entrega", "fecha_hora_local": "Fecha", "cedula": "Cédula",
    "trabajador": "Trabajador", "cargo": "Cargo", "sucursal": "Sucursal",
    "epp": "EPP", "categoria": "Categoría", "cantidad": "Cant.", "talla": "Talla",
    "motivo": "Motivo", "entregado_por": "Entregó",
}


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


@st.cache_data(ttl=120, show_spinner="Cargando datos...")
def cargar_detalle():
    filas = traer_todo(
        lambda: get_sb().table("v_entregas_detalle").select("*").order("detalle_id")
    )
    df = pd.DataFrame(filas)
    if df.empty:
        return df
    df["fecha"] = pd.to_datetime(df["fecha"]).dt.date
    df["mes"] = pd.to_datetime(df["fecha"]).dt.strftime("%Y-%m")
    df["fecha_hora_local"] = (
        pd.to_datetime(df["fecha_hora"], utc=True)
        .dt.tz_convert("America/Bogota")
        .dt.strftime("%Y-%m-%d %H:%M")
    )
    df["cargo"] = df["cargo"].fillna("Sin cargo")
    df["sucursal"] = df["sucursal"].fillna("Sin sucursal")
    df["categoria"] = df["categoria"].fillna("Otros")
    return df


@st.cache_data(ttl=120, show_spinner=False)
def cargar_cobertura():
    filas = traer_todo(
        lambda: get_sb().table("v_cobertura").select("*").order("trabajador_id")
    )
    df = pd.DataFrame(filas)
    if not df.empty:
        df["sucursal"] = df["sucursal"].fillna("Sin sucursal")
    return df


@st.cache_data(ttl=120, show_spinner=False)
def cargar_reposicion():
    filas = traer_todo(
        lambda: get_sb().table("v_reposicion").select("*")
        .order("trabajador_id").order("epp")
    )
    return pd.DataFrame(filas)


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


@st.cache_data(ttl=3600, show_spinner="Generando acta...")
def acta_bytes(entrega_id):
    e = (
        get_sb().table("entregas")
        .select(
            "id,fecha_hora,entregado_por,observaciones,firma_path,"
            "trabajadores(nombre,cedula,cargo,sucursal),"
            "entrega_detalle(cantidad,talla,motivo,epp_catalogo(nombre))"
        )
        .eq("id", entrega_id)
        .single()
        .execute()
        .data
    )
    e["fecha_local"] = (
        pd.to_datetime(e["fecha_hora"], utc=True)
        .tz_convert("America/Bogota")
        .strftime("%Y-%m-%d %H:%M")
    )
    firma = None
    if e.get("firma_path"):  # si falla la descarga, no se guarda en caché
        firma = get_sb().storage.from_(BUCKET_FIRMAS).download(e["firma_path"])
    return generar_acta_pdf(e, firma), firma, e["trabajadores"]["cedula"]


def rango_valido(rango):
    return isinstance(rango, (tuple, list)) and len(rango) == 2


# ---------- Vista 2: historial y actas ----------
def tab_historial():
    st.subheader("Historial y actas")
    df = cargar_detalle()
    if df.empty:
        st.info("Todavía no hay entregas registradas.")
        return

    c1, c2 = st.columns(2)
    min_f, max_f = df["fecha"].min(), df["fecha"].max()
    rango = c1.date_input(
        "Rango de fechas",
        value=(max(min_f, max_f - timedelta(days=30)), max_f),
        min_value=min_f, max_value=max_f, key="hist_rango",
    )
    texto = c2.text_input("Trabajador o cédula", key="hist_txt")
    if not rango_valido(rango):
        st.info("Selecciona la fecha inicial y la final.")
        return

    v = df[(df["fecha"] >= rango[0]) & (df["fecha"] <= rango[1])]
    if texto.strip():
        t = normalizar(texto)
        coincide = (
            v["trabajador"].map(normalizar).str.contains(t, regex=False)
            | v["cedula"].astype(str).str.contains(texto.strip(), regex=False)
        )
        v = v[coincide]
    if v.empty:
        st.warning("No hay entregas con esos filtros.")
        return

    vista = v[list(MAPA_DETALLE)].rename(columns=MAPA_DETALLE)
    st.caption(f"{v['entrega_id'].nunique()} actas · {int(v['cantidad'].sum())} unidades")
    st.dataframe(vista, use_container_width=True, hide_index=True)
    st.download_button(
        "⬇️ Descargar historial en Excel",
        data=a_excel({"Historial": vista}),
        file_name=f"Historial_EPP_{date.today():%Y%m%d}.xlsx",
        mime=XLSX_MIME,
    )

    st.markdown("---")
    st.markdown("**Detalle y descarga de acta firmada**")
    actas = v.drop_duplicates("entrega_id").sort_values("entrega_id", ascending=False)
    etiquetas = {
        int(r.entrega_id): f"Entrega #{int(r.entrega_id)} · {r.trabajador} ({r.fecha_hora_local})"
        for r in actas.itertuples()
    }
    elegida = st.selectbox(
        "Selecciona una entrega", list(etiquetas), format_func=lambda i: etiquetas[i]
    )
    try:
        pdf_bytes, firma, cedula = acta_bytes(elegida)
    except Exception as ex:
        st.warning(f"No se pudo generar el acta: {ex}")
        return

    col_img, col_pdf = st.columns(2)
    with col_img:
        if firma:
            st.image(firma, width=240, caption="Firma registrada")
        else:
            st.caption("Esta entrega no tiene firma.")
    with col_pdf:
        st.download_button(
            "📄 Descargar Acta en PDF",
            data=pdf_bytes,
            file_name=f"Acta_EPP_{elegida}_{cedula}.pdf",
            mime="application/pdf",
            type="primary",
            use_container_width=True,
        )


# ---------- Vista 3: control (KPIs) ----------
def barras(serie, titulo, horizontal=False):
    d = serie.reset_index()
    d.columns = ["Item", "Unidades"]
    if horizontal:
        fig = px.bar(d.sort_values("Unidades"), x="Unidades", y="Item", orientation="h", title=titulo)
    else:
        fig = px.bar(d, x="Item", y="Unidades", title=titulo)
    fig.update_layout(
        template="plotly_dark", height=320, xaxis_title=None, yaxis_title=None,
        margin=dict(l=0, r=0, t=40, b=0),
    )
    st.plotly_chart(fig, use_container_width=True)


def tab_kpis():
    st.subheader("Control de EPP")
    cob_all = cargar_cobertura()
    if cob_all.empty:
        st.info("No hay trabajadores activos cargados.")
        return

    suc = st.selectbox("Sucursal", ["Todas"] + sorted(cob_all["sucursal"].unique()), key="kpi_suc")
    cob = cob_all if suc == "Todas" else cob_all[cob_all["sucursal"] == suc]

    # --- 1. Cobertura ---
    st.markdown("### Cobertura de dotación")
    activos = len(cob)
    dotados = int((cob["entregas"] > 0).sum())
    pct = dotados / activos * 100 if activos else 0
    k1, k2, k3 = st.columns(3)
    k1.metric("Trabajadores activos", f"{activos:,}")
    k2.metric("Con entrega registrada", f"{dotados:,}")
    k3.metric("Cobertura", f"{pct:.1f}%")
    st.progress(min(pct / 100, 1.0))
    st.caption("Mide el avance del registro en el sistema: trabajadores activos con al menos una entrega.")

    por_suc = (
        cob.groupby("sucursal")
        .agg(Activos=("trabajador_id", "count"), Dotados=("entregas", lambda s: int((s > 0).sum())))
        .reset_index()
    )
    por_suc["Cobertura %"] = (por_suc["Dotados"] / por_suc["Activos"] * 100).round(1)
    por_suc = por_suc.sort_values("Cobertura %").rename(columns={"sucursal": "Sucursal"})
    st.dataframe(por_suc, hide_index=True, use_container_width=True)

    sin_dot = (
        cob[cob["entregas"] == 0][["cedula", "nombre", "cargo", "sucursal"]]
        .rename(columns={"cedula": "Cédula", "nombre": "Nombre", "cargo": "Cargo", "sucursal": "Sucursal"})
    )
    with st.expander(f"Trabajadores sin ninguna entrega ({len(sin_dot):,})"):
        st.dataframe(sin_dot, hide_index=True, use_container_width=True)

    hojas = {"Sin dotacion": sin_dot, "Cobertura por sucursal": por_suc}

    # --- 2. Reposiciones ---
    st.markdown("### Reposiciones")
    rep = cargar_reposicion()
    if rep.empty:
        st.info(
            "Aún no hay EPP con vida útil definida (columna vida_util_dias del catálogo). "
            "Al definirla, aquí verás qué está vencido o por vencer."
        )
    else:
        rep = rep.assign(sucursal=rep["sucursal"].fillna("Sin sucursal"))
        if suc != "Todas":
            rep = rep[rep["sucursal"] == suc].copy()
        hoy = pd.Timestamp.now(tz="America/Bogota").date()
        rep["vence"] = pd.to_datetime(rep["vence"], utc=True).dt.tz_convert("America/Bogota").dt.date
        rep["dias"] = rep["vence"].map(lambda d: (d - hoy).days)
        pendientes = rep[rep["dias"] <= 30].sort_values("dias")
        r1, r2 = st.columns(2)
        r1.metric("Vencidos", int((rep["dias"] < 0).sum()))
        r2.metric("Vencen en 30 días", int(((rep["dias"] >= 0) & (rep["dias"] <= 30)).sum()))
        tabla = pendientes[["trabajador", "cedula", "sucursal", "epp", "vence", "dias"]].rename(columns={
            "trabajador": "Trabajador", "cedula": "Cédula", "sucursal": "Sucursal",
            "epp": "EPP", "vence": "Vence", "dias": "Días restantes",
        })
        if tabla.empty:
            st.success("No hay reposiciones vencidas ni próximas a vencer.")
        else:
            st.dataframe(tabla, hide_index=True, use_container_width=True)
            hojas["Reposiciones"] = tabla.assign(Vence=tabla["Vence"].astype(str))

    # --- 3. Actividad del período ---
    st.markdown("### Actividad del período")
    det = cargar_detalle()
    if suc != "Todas" and not det.empty:
        det = det[det["sucursal"] == suc]
    if det.empty:
        st.info("Aún no hay entregas para mostrar actividad.")
    else:
        min_f, max_f = det["fecha"].min(), det["fecha"].max()
        rango = st.date_input(
            "Período",
            value=(max(min_f, max_f - timedelta(days=90)), max_f),
            min_value=min_f, max_value=max_f, key="kpi_rango",
        )
        if not rango_valido(rango):
            st.info("Selecciona la fecha inicial y la final.")
        else:
            per = det[(det["fecha"] >= rango[0]) & (det["fecha"] <= rango[1])]
            if per.empty:
                st.warning("No hay entregas en ese período.")
            else:
                unidades = int(per["cantidad"].sum())
                deterioro = int(per.loc[per["motivo"] == "DETERIORO", "cantidad"].sum())
                reposicion = int(per.loc[per["motivo"] == "REPOSICION", "cantidad"].sum())
                a1, a2, a3, a4 = st.columns(4)
                a1.metric("Actas", f"{per['entrega_id'].nunique():,}")
                a2.metric("Trabajadores atendidos", f"{per['trabajador_id'].nunique():,}")
                a3.metric("Unidades", f"{unidades:,}")
                a4.metric("% por deterioro", f"{deterioro / unidades * 100:.1f}%")
                st.caption(f"Reposiciones: {reposicion:,} unidades · Deterioro: {deterioro:,} unidades")

                g1, g2 = st.columns(2)
                with g1:
                    barras(
                        per.groupby("epp")["cantidad"].sum().sort_values(ascending=False).head(10),
                        "Top 10 EPP entregados", horizontal=True,
                    )
                with g2:
                    det_epp = per[per["motivo"] == "DETERIORO"].groupby("epp")["cantidad"].sum()
                    if det_epp.empty:
                        st.info("Sin entregas por deterioro en el período.")
                    else:
                        barras(
                            det_epp.sort_values(ascending=False).head(8),
                            "EPP con más deterioro", horizontal=True,
                        )

                g3, g4 = st.columns(2)
                with g3:
                    barras(per.groupby("mes")["cantidad"].sum().sort_index(), "Unidades por mes")
                with g4:
                    if suc == "Todas":
                        activos_suc = cob_all.groupby("sucursal").size()
                        u_suc = per.groupby("sucursal")["cantidad"].sum()
                        ratio = (u_suc / activos_suc).dropna().round(2).sort_values(ascending=False)
                        barras(ratio, "Unidades por trabajador activo")
                    else:
                        barras(
                            per.groupby("categoria")["cantidad"].sum().sort_values(ascending=False),
                            "Unidades por categoría",
                        )

                hojas["Detalle"] = per[list(MAPA_DETALLE)].rename(columns=MAPA_DETALLE)
                hojas["Por EPP"] = (
                    per.groupby(["categoria", "epp"])["cantidad"].sum()
                    .reset_index().sort_values("cantidad", ascending=False)
                )

    st.markdown("---")
    st.download_button(
        "⬇️ Descargar indicadores en Excel",
        data=a_excel(hojas),
        file_name=f"Control_EPP_{date.today():%Y%m%d}.xlsx",
        mime=XLSX_MIME,
        type="primary",
    )


# ---------- Programa principal ----------
if not check_password():
    st.stop()

encabezado()
cargar_activos()   # precarga: las búsquedas quedan instantáneas
cargar_catalogo()

VISTAS = ["Registrar entrega", "Historial y Actas", "Control (KPIs)"]
vista = st.segmented_control(
    "Vista", VISTAS, default=VISTAS[0], key="vista", label_visibility="collapsed"
) or VISTAS[0]

if vista == VISTAS[0]:
    tab_registro()
elif vista == VISTAS[1]:
    tab_historial()
else:
    tab_kpis()
