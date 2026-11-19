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

st.set_page_config(page_title="Entrega de EPP", page_icon="🦺", layout="centered")

MOTIVOS = ["NUEVO", "REPOSICION", "DETERIORO"]
LOGO = "logo.png"
COLUMNAS = 3  # tarjetas por fila: prueba 2, 3 o 4
BUCKET_FIRMAS = "firmas-epp"


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
    entregado_por = st.text_input("Entregado por", key=f"ep{n}")
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
        if not entregado_por.strip():
            errores.append("Indica quién entrega.")
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


# ---------- Programa principal ----------
if not check_password():
    st.stop()

encabezado()
t1, t2 = st.tabs(["Registrar entrega", "Historial"])
with t1:
    tab_registro()
with t2:
    tab_historial()
