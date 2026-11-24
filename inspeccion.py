"""Inspección de uso de EPP y dotación (formato del SG-SST) y medidas de control."""
import random
import uuid
from datetime import datetime, timedelta, timezone

import pandas as pd
import streamlit as st
from streamlit_drawable_canvas import st_canvas

from comun import (
    BUCKET_FIRMAS,
    acceso,
    buscar_trabajadores,
    cargar_activos,
    fecha_local,
    firma_a_png,
    get_sb,
    traer_todo,
)

OPCIONES = ["C", "NC", "N/A"]  # Cumple / No cumple / No aplica


# ---------- Datos ----------
@st.cache_data(ttl=300, show_spinner=False)
def cargar_catalogos():
    sb = get_sb()

    def leer(tabla, orden):
        return sb.table(tabla).select("*").eq("activo", True).order(orden).execute().data

    return {
        "items": leer("insp_items", "orden"),
        "causas": leer("insp_causas", "codigo"),
        "medidas": leer("insp_medidas", "codigo"),
        "no_permitidos": leer("insp_no_permitidos", "id"),
    }


@st.cache_data(ttl=60, show_spinner=False)
def inspeccionados_desde(dias):
    """Ids de trabajadores inspeccionados en los últimos `dias` días."""
    desde = (datetime.now(timezone.utc) - timedelta(days=dias)).strftime("%Y-%m-%dT%H:%M:%SZ")
    filas = traer_todo(
        lambda: get_sb().table("inspecciones_uso").select("id,trabajador_id")
        .gte("fecha_hora", desde).order("id")
    )
    return {f["trabajador_id"] for f in filas}


@st.cache_data(ttl=120, show_spinner=False)
def cargar_antecedentes(trabajador_id):
    """Solo fecha, resultado e ítems incumplidos (sin causas ni medidas)."""
    r = (
        get_sb().table("v_inspecciones_uso")
        .select("inspeccion_id,fecha_hora,cumple,items_incumplidos")
        .eq("trabajador_id", trabajador_id)
        .order("fecha_hora", desc=True)
        .limit(5)
        .execute()
    )
    return r.data


@st.cache_data(ttl=60, show_spinner=False)
def cargar_inspecciones():
    filas = traer_todo(
        lambda: get_sb().table("v_inspecciones_uso").select("*")
        .order("inspeccion_id", desc=True)
    )
    return pd.DataFrame(filas)


def limpiar_cache():
    inspeccionados_desde.clear()
    cargar_antecedentes.clear()
    cargar_inspecciones.clear()


# ---------- Utilidades ----------
def sortear(activos, excluidos, area):
    candidatos = [
        w for w in activos
        if w["id"] not in excluidos and (area == "Todas" or w["area"] == area)
    ]
    return random.choice(candidatos) if candidatos else None


def marcar_todo(claves):
    for k in claves:
        st.session_state[k] = "C"


def txt(valor):
    if valor is None or (isinstance(valor, float) and pd.isna(valor)) or valor == "":
        return "—"
    return str(valor)


# ---------- Vista: inspección ----------
def tab_inspeccion():
    if not acceso("acceso_inspeccion", "INSPECCION_PASSWORD", "Inspección de uso de EPP y dotación"):
        return

    st.subheader("Inspección de uso de EPP y dotación")
    n = st.session_state.setdefault("insp_n", 0)  # cambia para limpiar el formulario
    msg = st.session_state.pop("insp_ok", None)
    if msg:
        st.success(msg)

    cat = cargar_catalogos()
    activos = cargar_activos()

    # 1. Persona inspeccionada
    st.markdown("### 1. Persona inspeccionada")
    modo = st.segmented_control(
        "Selección", ["Buscar", "Sorteo aleatorio"], default="Buscar", key=f"im{n}"
    ) or "Buscar"
    trabajador = None
    if modo == "Buscar":
        q = st.text_input("Buscar por cédula o nombre", key=f"iq{n}")
        if q and len(q.strip()) < 3:
            st.caption("Escribe al menos 3 caracteres.")
        elif q:
            resultados = buscar_trabajadores(q)
            if not resultados:
                st.warning("No se encontró ningún trabajador activo con ese dato.")
            else:
                por_id = {t["id"]: t for t in resultados}
                tid = st.selectbox(
                    "Selecciona a la persona",
                    list(por_id),
                    format_func=lambda i: f"{por_id[i]['nombre']} · {por_id[i]['cedula']}",
                    key=f"it{n}",
                )
                trabajador = por_id[tid]
    else:
        areas = ["Todas"] + sorted({w["area"] for w in activos})
        c1, c2 = st.columns(2)
        area = c1.selectbox("Área", areas, key=f"ia{n}")
        dias = c2.number_input(
            "Excluir inspeccionados en los últimos (días)", 0, 365, 90, key=f"id{n}"
        )
        if st.button("🎲 Sortear", key=f"is{n}", use_container_width=True):
            excluidos = inspeccionados_desde(int(dias)) if dias else set()
            elegido = sortear(activos, excluidos, area)
            if elegido is None:
                st.warning("No hay trabajadores disponibles con esos criterios.")
            else:
                st.session_state[f"insp_sorteado{n}"] = elegido
        trabajador = st.session_state.get(f"insp_sorteado{n}")

    if trabajador:
        st.info(
            f"**{trabajador['nombre']}** · C.C. {trabajador['cedula']}\n\n"
            f"Cargo: {txt(trabajador['cargo'])} · Área: {trabajador['area']} · "
            f"Sucursal: {txt(trabajador['sucursal'])}"
        )
        antecedentes = cargar_antecedentes(trabajador["id"])
        if antecedentes:
            with st.expander(f"Inspecciones previas ({len(antecedentes)})"):
                for a in antecedentes:
                    resultado = "Cumple" if a["cumple"] else "No cumple"
                    detalle = "" if a["cumple"] else f" — {txt(a['items_incumplidos'])}"
                    st.write(f"{fecha_local(a['fecha_hora'])} · {resultado}{detalle}")

    # 2. Calificación
    st.markdown("### 2. Calificación de uso")
    st.caption("C: Cumple · NC: No cumple · N/A: No aplica")
    estados = {}
    nombres_item = {}
    for tipo, titulo in (("EPP", "Elementos de protección personal"), ("DOTACION", "Dotación")):
        items = [i for i in cat["items"] if i["tipo"] == tipo]
        claves = [f"ii{i['id']}_{n}" for i in items]
        st.markdown(f"**{titulo}**")
        st.button(
            "✅ Todo cumple", key=f"todo_{tipo}_{n}",
            on_click=marcar_todo, args=(claves,),
        )
        for i, clave in zip(items, claves):
            estados[i["id"]] = st.segmented_control(i["nombre"], OPCIONES, key=clave)
            nombres_item[i["id"]] = i["nombre"]

    hay_nc = any(v == "NC" for v in estados.values())

    # 3. Hallazgos
    st.markdown("### 3. Hallazgos")
    causas_sel = []
    por_causa = {c["id"]: c for c in cat["causas"]}
    if hay_nc:
        causas_sel = st.multiselect(
            "Razón por la cual no usa los EPP o la dotación",
            list(por_causa),
            format_func=lambda i: f"{por_causa[i]['codigo']}. {por_causa[i]['texto']}",
            key=f"ic{n}",
        )
    else:
        st.caption("Si algún ítem queda en NC, aquí se piden las causas del incumplimiento.")
    por_np = {x["id"]: x for x in cat["no_permitidos"]}
    no_perm_sel = st.multiselect(
        "Elementos no permitidos hallados (opcional)",
        list(por_np),
        format_func=lambda i: por_np[i]["nombre"],
        key=f"inp{n}",
    )

    # 4. Tallas
    st.markdown("### 4. Tallas (opcional)")
    f1 = st.columns(2)
    f2 = st.columns(2)
    tallas = {
        "zapato": f1[0].text_input("Zapato", key=f"tz{n}").strip(),
        "bata": f1[1].text_input("Bata", key=f"tb{n}").strip(),
        "polo": f2[0].text_input("Polo", key=f"tp{n}").strip(),
        "jeans": f2[1].text_input("Jeans", key=f"tj{n}").strip(),
    }

    # 5. Cierre y firma
    st.markdown("### 5. Cierre y firma")
    inspector = st.text_input("Nombre del inspector", key=f"iin{n}")
    observaciones = st.text_area("Observaciones", key=f"io{n}")
    st.markdown("**Firma de la persona inspeccionada**")
    st.caption("Firma con el dedo dentro del recuadro.")
    lienzo = st_canvas(
        stroke_width=3,
        stroke_color="#000000",
        background_color="#FFFFFF",
        height=160,
        width=320,
        drawing_mode="freedraw",
        display_toolbar=True,
        key=f"ifirma{n}",
    )
    firmado = bool(lienzo.json_data and lienzo.json_data.get("objects"))

    if st.button("Guardar inspección", type="primary", use_container_width=True, key=f"isave{n}"):
        errores = []
        if trabajador is None:
            errores.append("Selecciona a la persona inspeccionada.")
        faltan = [nombres_item[i] for i, v in estados.items() if v is None]
        if faltan:
            muestra = ", ".join(faltan[:5]) + ("…" if len(faltan) > 5 else "")
            errores.append(f"Faltan {len(faltan)} ítems por calificar: {muestra}")
        if hay_nc and not causas_sel:
            errores.append("Indica la causa del incumplimiento.")
        codigos_sel = {por_causa[i]["codigo"] for i in causas_sel}
        if 8 in codigos_sel and not no_perm_sel:
            errores.append("Indica qué elementos no permitidos se encontraron (causa 8).")
        if not inspector.strip():
            errores.append("Indica el nombre del inspector.")
        if not firmado:
            errores.append("Falta la firma de la persona inspeccionada.")

        if errores:
            for err in errores:
                st.error(err)
            return

        items_payload = [
            {"item_id": i, "estado": "NA" if v == "N/A" else v} for i, v in estados.items()
        ]
        png = firma_a_png(lienzo.image_data)
        ruta = f"inspecciones/{datetime.now():%Y/%m}/{uuid.uuid4().hex}.png"
        subida = False
        guardado = False
        with st.spinner("Guardando inspección..."):
            try:
                get_sb().storage.from_(BUCKET_FIRMAS).upload(
                    ruta, png, {"content-type": "image/png"}
                )
                subida = True
                r = get_sb().rpc("registrar_inspeccion_uso", {
                    "p_trabajador_id": trabajador["id"],
                    "p_inspector": inspector.strip(),
                    "p_observaciones": observaciones.strip() or None,
                    "p_tallas": {k: v for k, v in tallas.items() if v},
                    "p_items": items_payload,
                    "p_causas": causas_sel,
                    "p_no_permitidos": no_perm_sel,
                    "p_firma_path": ruta,
                }).execute()
                if hay_nc:
                    st.session_state["insp_ok"] = (
                        f"⚠️ Inspección #{r.data} registrada con hallazgos. "
                        "Queda pendiente la medida de control (SST y RR. HH.)."
                    )
                else:
                    st.session_state["insp_ok"] = f"✅ Inspección #{r.data} registrada: cumple."
                guardado = True
            except Exception as ex:
                st.error(f"No se pudo guardar: {ex}")
                if subida and not guardado:
                    try:  # evita dejar una firma huérfana en el bucket
                        get_sb().storage.from_(BUCKET_FIRMAS).remove([ruta])
                    except Exception:
                        pass

        if guardado:
            limpiar_cache()
            st.session_state["insp_n"] = n + 1
            st.rerun()


# ---------- Vista: medidas de control (SST y RR. HH.) ----------
def tab_medidas():
    if not acceso("acceso_medidas", "MEDIDAS_PASSWORD", "Medidas de control (SST y RR. HH.)"):
        return

    st.subheader("Medidas de control")
    st.caption(
        "Las medidas las define SST junto con RR. HH. a partir de los hallazgos "
        "de cada inspección."
    )
    msg = st.session_state.pop("med_ok", None)
    if msg:
        st.success(msg)

    df = cargar_inspecciones()
    if df.empty:
        st.info("Todavía no hay inspecciones registradas.")
        return

    df = df.assign(Fecha=df["fecha_hora"].map(fecha_local))
    pend = df[(~df["cumple"].astype(bool)) & (df["n_medidas"] == 0)]
    resueltas = df[df["n_medidas"] > 0]

    m1, m2, m3 = st.columns(3)
    m1.metric("Inspecciones", f"{len(df):,}")
    m2.metric("Pendientes de medida", f"{len(pend):,}")
    m3.metric("Con medida asignada", f"{len(resueltas):,}")

    st.markdown("### Pendientes de medida")
    if pend.empty:
        st.success("No hay inspecciones con hallazgos pendientes de medida.")
    else:
        tabla = pend[[
            "inspeccion_id", "Fecha", "trabajador", "cedula", "area",
            "items_incumplidos", "causas",
        ]].rename(columns={
            "inspeccion_id": "Inspección", "trabajador": "Trabajador", "cedula": "Cédula",
            "area": "Área", "items_incumplidos": "Ítems incumplidos", "causas": "Causas",
        })
        st.dataframe(tabla, hide_index=True, use_container_width=True)

        etiquetas = {
            int(r.inspeccion_id): f"#{int(r.inspeccion_id)} · {r.trabajador} · {r.Fecha}"
            for r in pend.itertuples()
        }
        elegida = st.selectbox(
            "Selecciona una inspección", list(etiquetas), format_func=lambda i: etiquetas[i]
        )
        fila = pend[pend["inspeccion_id"] == elegida].iloc[0]
        st.markdown(f"**Inspector:** {txt(fila['inspector'])}")
        st.markdown(f"**Ítems incumplidos:** {txt(fila['items_incumplidos'])}")
        st.markdown(f"**Causas:** {txt(fila['causas'])}")
        st.markdown(f"**Elementos no permitidos:** {txt(fila['no_permitidos'])}")
        st.markdown(f"**Observaciones:** {txt(fila['observaciones'])}")

        cat = cargar_catalogos()
        por_medida = {m["id"]: m for m in cat["medidas"]}
        sel = st.multiselect(
            "Medidas de control definidas",
            list(por_medida),
            format_func=lambda i: f"{por_medida[i]['codigo']}. {por_medida[i]['texto']}",
            key=f"med_sel_{elegida}",
        )
        definida_por = st.text_input(
            "Definidas por (nombres de SST y RR. HH.)", key=f"med_por_{elegida}"
        )
        if st.button("Guardar medidas", type="primary", key=f"med_save_{elegida}"):
            if not sel:
                st.error("Selecciona al menos una medida.")
            elif not definida_por.strip():
                st.error("Indica quién define las medidas.")
            else:
                guardado = False
                try:
                    get_sb().rpc("registrar_medidas_inspeccion", {
                        "p_inspeccion_id": int(elegida),
                        "p_medidas": sel,
                        "p_definida_por": definida_por.strip(),
                    }).execute()
                    st.session_state["med_ok"] = f"✅ Medidas registradas para la inspección #{int(elegida)}."
                    guardado = True
                except Exception as ex:
                    st.error(f"No se pudieron guardar las medidas: {ex}")
                if guardado:
                    cargar_inspecciones.clear()
                    st.rerun()

    st.markdown("### Con medida asignada")
    if resueltas.empty:
        st.caption("Aún no hay inspecciones con medidas asignadas.")
    else:
        tabla_r = resueltas[[
            "inspeccion_id", "Fecha", "trabajador", "area", "items_incumplidos", "medidas",
        ]].rename(columns={
            "inspeccion_id": "Inspección", "trabajador": "Trabajador", "area": "Área",
            "items_incumplidos": "Ítems incumplidos", "medidas": "Medidas",
        })
        st.dataframe(tabla_r, hide_index=True, use_container_width=True)
