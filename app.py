import hmac
import os
import re

import pandas as pd
import streamlit as st
from supabase import create_client

st.set_page_config(page_title="Entrega de EPP", page_icon="🦺", layout="wide")

MOTIVOS = ["NUEVO", "REPOSICION", "DETERIORO"]
LOGO = "logo.png"


# ---------- Conexión ----------
@st.cache_resource
def get_sb():
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_SERVICE_KEY"])


# ---------- Acceso con contraseña ----------
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


def encabezado():
    if os.path.exists(LOGO):
        st.image(LOGO, width=140)
    st.title("Entrega de EPP")


# ---------- Datos ----------
@st.cache_data(ttl=300)
def cargar_catalogo():
    r = (
        get_sb().table("epp_catalogo")
        .select("id,nombre,requiere_talla,imagen_url")
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

    # Estilo CSS para alinear al centro y ajustar el botón al ancho exacto de la imagen (120px)
    st.markdown(
        """
        <style>
        .st-key-grid_epp [data-testid="column"] {
            display: flex;
            flex-direction: column;
            align-items: center;
        }
        .st-key-grid_epp button {
            width: 120px !important;
            max-width: 120px !important;
            white-space: normal !important;
            font-size: 0.85rem !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    lista = list(catalogo.values())
    with st.container(key="grid_epp"):
        for k in range(0, len(lista), 3):
            cols = st.columns(3)
            for col, e in zip(cols, lista[k:k + 3]):
                with col:
                    if e.get("imagen_url"):
                        st.image(e["imagen_url"], width=120)
                    else:
                        st.markdown(
                            "<div style='font-size:2.5rem;text-align:center;height:120px;display:flex;align-items:center;justify-content:center;'>🦺</div>",
                            unsafe_allow_html=True,
                        )
                    marcado = e["id"] in seleccion
                    st.button(
                        ("✓ " if marcado else "") + e["nombre"],
                        key=f"b{e['id']}_{n}",
                        type="primary" if marcado else "secondary",
                        use_container_width=False,
                        on_click=alternar,
                        args=(clave, e["id"]),
                    )
    return seleccion


# ---------- Pestaña 1: registrar ----------
def tab_registro():
    n = st.session_state.setdefault("form_n", 0)

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
        e = catalogo[i]
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

        if errores:
            for err in errores:
                st.error(err)
            return

        payload = [{k: v for k, v in it.items() if not k.startswith("_")} for it in items]
        guardado = False
        try:
            r = get_sb().rpc("registrar_entrega", {
                "p_trabajador_id": trabajador["id"],
                "p_entregado_por": entregado_por.strip(),
                "p_observaciones": observaciones.strip() or None,
                "p_items": payload,
            }).execute()
            st.session_state["ok_msg"] = (
                f"✅ Entrega #{r.data} registrada para {trabajador['nombre']}."
            )
            guardado = True
        except Exception as ex:
            st.error(f"No se pudo guardar: {ex}")

        if guardado:
            st.session_state["form_n"] = n + 1
            st.rerun()


# ---------- Pestaña 2: historial ----------
def tab_historial():
    st.subheader("Últimas 50 entregas")
    r = (
        get_sb().table("entregas")
        .select(
            "id,fecha_hora,entregado_por,"
            "trabajadores(nombre,cedula),"
            "entrega_detalle(cantidad,talla,motivo,epp_catalogo(nombre))"
        )
        .order("fecha_hora", desc=True)
        .limit(50)
        .execute()
    )
    filas = []
    for e in r.data:
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
    if filas:
        st.dataframe(pd.DataFrame(filas), use_container_width=True, hide_index=True)
    else:
        st.info("Todavía no hay entregas registradas.")


# ---------- Programa principal ----------
if not check_password():
    st.stop()

encabezado()
t1, t2 = st.tabs(["Registrar entrega", "Historial"])
with t1:
    tab_registro()
with t2:
    tab_historial()
