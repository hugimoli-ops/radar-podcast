#!/usr/bin/env python3
"""
Robot que actualiza la web de noticias del podcast.

Lo ejecuta GitHub Actions cada pocos minutos (ver .github/workflows/actualizar.yml):
  1. Lee las fuentes de fuentes.json (RSS/Atom).
  2. Guarda las noticias nuevas en data/noticias.json (lo que muestra la web).
  3. Avisa por Telegram de las noticias importantes y de los resúmenes nuevos
     que Claude haya escrito en data/resumenes.json.

Solo usa la librería estándar de Python: no hay que instalar nada.
"""
import concurrent.futures as cf
import datetime as dt
import email.utils
import hashlib
import html
import json
import os
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DATA = RAIZ / "data"
FUENTES = RAIZ / "fuentes.json"
NOTICIAS = DATA / "noticias.json"
RESUMENES = DATA / "resumenes.json"
ESTADO = DATA / "estado.json"
META = DATA / "meta.json"

DIAS_A_GUARDAR = 10          # las noticias más antiguas se borran de la web
MAX_NOTICIAS = 2500
MAX_POR_FUENTE = 15
MAX_AVISOS_POR_RONDA = 5     # más de esto se agrupa en un solo mensaje
HORAS_SILENCIO = (0, 8)      # de 00:00 a 08:00 (hora de Madrid) no se envía nada; se junta en un resumen
ZONA = "Europe/Madrid"

UA = "Mozilla/5.0 (compatible; PodcastNoticiasBot/1.0; +https://github.com)"
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
WEB = os.environ.get("SITE_URL", "").strip()


# ----------------------------------------------------------------- utilidades
def leer_json(ruta, defecto):
    try:
        return json.loads(ruta.read_text(encoding="utf-8"))
    except Exception:
        return defecto


def escribir_json(ruta, datos, compacto=False):
    ruta.parent.mkdir(parents=True, exist_ok=True)
    if compacto:
        texto = json.dumps(datos, ensure_ascii=False, separators=(",", ":"))
    else:
        texto = json.dumps(datos, ensure_ascii=False, indent=2)
    ruta.write_text(texto + "\n", encoding="utf-8")


def ahora():
    return dt.datetime.now(dt.timezone.utc)


def hora_madrid():
    try:
        from zoneinfo import ZoneInfo
        return ahora().astimezone(ZoneInfo(ZONA))
    except Exception:
        return ahora() + dt.timedelta(hours=1)


def limpiar(texto, largo=280):
    texto = re.sub(r"<[^>]+>", " ", texto or "")
    texto = html.unescape(texto)
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto[: largo - 1] + "…" if len(texto) > largo else texto


def id_de(enlace, titulo):
    base = enlace or titulo
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]


def clave_titulo(titulo):
    # Para no repetir la misma historia contada por varios medios
    t = re.sub(r"\s+-\s+[^-]+$", "", titulo.lower())  # quita " - Medio" de Google News
    t = re.sub(r"[^a-z0-9áéíóúñü ]", "", t)
    return " ".join(t.split()[:9])


def fecha_de(texto):
    if not texto:
        return None
    texto = texto.strip()
    try:
        d = email.utils.parsedate_to_datetime(texto)
    except Exception:
        d = None
    if d is None:
        try:
            d = dt.datetime.fromisoformat(texto.replace("Z", "+00:00"))
        except Exception:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d.astimezone(dt.timezone.utc)


# ----------------------------------------------------------------- lectura de fuentes
def descargar(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read()


def hijo(el, *nombres):
    for n in nombres:
        for c in el:
            if c.tag.split("}")[-1] == n:
                return c
    return None


def texto_de(el, *nombres):
    c = hijo(el, *nombres)
    return (c.text or "").strip() if c is not None and c.text else ""


def parsear(xml_bytes, fuente):
    raiz = ET.fromstring(xml_bytes)
    es_google = "news.google.com" in fuente["url"]
    salida = []
    entradas = [e for e in raiz.iter() if e.tag.split("}")[-1] in ("item", "entry")]
    for e in entradas[: fuente.get("max", MAX_POR_FUENTE)]:
        titulo = limpiar(texto_de(e, "title"), 300)
        enlace = texto_de(e, "link")
        if not enlace:
            l = hijo(e, "link")
            if l is not None:
                enlace = l.get("href", "")
        if not titulo or not enlace:
            continue
        medio = fuente["nombre"]
        s = hijo(e, "source")
        if s is not None and s.text:
            medio = s.text.strip()
        fecha = fecha_de(texto_de(e, "pubDate", "published", "updated", "date")) or ahora()
        resumen = "" if es_google else limpiar(texto_de(e, "description", "summary", "content"))
        salida.append({
            "id": id_de(enlace, titulo),
            "titulo": titulo,
            "enlace": enlace,
            "medio": medio,
            "fuente": fuente["nombre"],
            "seccion": fuente["seccion"],
            "fecha": fecha.isoformat(timespec="seconds"),
            "resumen": resumen,
        })
    return salida


def leer_fuente(fuente):
    try:
        return fuente, parsear(descargar(fuente["url"]), fuente), None
    except Exception as err:
        return fuente, [], f"{type(err).__name__}: {err}"[:200]


# ----------------------------------------------------------------- Telegram
TG = {"enviados": 0, "errores": 0, "ultimo_error": ""}


def telegram(texto, vista_previa=False):
    if not TOKEN or not CHAT:
        return False
    datos = urllib.parse.urlencode({
        "chat_id": CHAT,
        "text": texto[:4000],
        "parse_mode": "HTML",
        "disable_web_page_preview": "false" if vista_previa else "true",
    }).encode()
    try:
        urllib.request.urlopen(f"https://api.telegram.org/bot{TOKEN}/sendMessage", data=datos, timeout=20).read()
        TG["enviados"] += 1
        return True
    except Exception as err:
        print("Error enviando a Telegram:", err)
        TG["errores"] += 1
        TG["ultimo_error"] = f"{type(err).__name__}: {err}"[:200]
        return False


def e(t):
    return html.escape(t or "", quote=False)


def enlace_web(ancla=""):
    if not WEB:
        return ""
    return WEB.rstrip("/") + "/" + (f"#{ancla}" if ancla else "")


def mensaje_noticia(n, iconos):
    ic = iconos.get(n["seccion"], "📰")
    partes = [f"{ic} <b>{e(n['titulo'])}</b>", f"<i>{e(n['medio'])}</i>", f'<a href="{e(n["enlace"])}">Leer la noticia</a>']
    if WEB:
        partes[-1] += f' · <a href="{e(enlace_web("n-" + n["id"]))}">Ver en la web</a>'
    return "\n".join(partes)


def mensaje_resumen(r, iconos):
    ic = iconos.get(r.get("seccion", ""), "🧠")
    lineas = [f"🧠 {ic} <b>{e(r.get('titulo', ''))}</b>"]
    if r.get("para_tontos"):
        lineas.append(f"\n<b>Para tontos:</b> {e(r['para_tontos'])}")
    if r.get("por_que_importa"):
        lineas.append(f"\n<b>Por qué importa:</b> {e(r['por_que_importa'])}")
    if r.get("de_que_hablar"):
        lineas.append(f"\n<b>Para el podcast:</b> {e(r['de_que_hablar'])}")
    link = enlace_web("r-" + r["id"]) if WEB else r.get("enlace", "")
    if link:
        lineas.append(f'\n<a href="{e(link)}">Leer la explicación completa</a>')
    return "\n".join(lineas)


def mensaje_agrupado(lista, titulo, iconos):
    lineas = [f"<b>{e(titulo)}</b>"]
    for n in lista[:12]:
        ic = iconos.get(n.get("seccion", ""), "📰")
        lineas.append(f'{ic} <a href="{e(n.get("enlace", ""))}">{e(n.get("titulo", ""))}</a>')
    if len(lista) > 12:
        lineas.append(f"…y {len(lista) - 12} más.")
    if WEB:
        lineas.append(f'\n<a href="{e(enlace_web())}">Abrir la web</a>')
    return "\n".join(lineas)


# ----------------------------------------------------------------- principal
def es_importante(n, cfg, fuentes_alerta, patrones):
    if n["fuente"] in fuentes_alerta:
        return True
    if n["seccion"] not in cfg.get("secciones_con_alertas", []):
        return False
    t = n["titulo"].lower()
    return any(p.search(t) for p in patrones)


def compilar(palabras):
    patrones = []
    for p in palabras:
        p = p.lower()
        if re.fullmatch(r"[\wáéíóúñü ]+", p):
            patrones.append(re.compile(r"\b" + re.escape(p) + r"\b"))
        else:
            patrones.append(re.compile(re.escape(p)))
    return patrones


def main():
    cfg = leer_json(FUENTES, {})
    fuentes = cfg.get("fuentes", [])
    iconos = {s["id"]: s.get("icono", "") for s in cfg.get("secciones", [])}
    fuentes_alerta = {f["nombre"] for f in fuentes if f.get("alerta")}
    patrones = compilar(cfg.get("palabras_alerta", []))

    noticias = leer_json(NOTICIAS, [])
    resumenes = leer_json(RESUMENES, [])
    estado = leer_json(ESTADO, {})
    primera_vez = not estado.get("inicializado")
    vistos = set(estado.get("vistos", []))
    claves_vistas = set(estado.get("claves", []))
    pendientes = estado.get("pendientes", [])

    # 1. Leer todas las fuentes a la vez
    nuevas, fallidas, ok = [], {}, 0
    with cf.ThreadPoolExecutor(max_workers=12) as ex:
        for fuente, items, err in ex.map(leer_fuente, fuentes):
            if err:
                fallidas[fuente["nombre"]] = err
                continue
            ok += 1
            for n in items:
                if n["id"] in vistos:
                    continue
                vistos.add(n["id"])
                nuevas.append(n)

    # 2. Guardar en la web (quitando lo antiguo)
    limite = ahora() - dt.timedelta(days=DIAS_A_GUARDAR)
    todas = {n["id"]: n for n in noticias}
    for n in nuevas:
        todas[n["id"]] = n
    lista = [n for n in todas.values() if (fecha_de(n["fecha"]) or ahora()) >= limite]
    lista.sort(key=lambda n: n["fecha"], reverse=True)
    lista = lista[:MAX_NOTICIAS]

    # 3. Decidir qué avisos mandar
    avisos = []
    for n in sorted(nuevas, key=lambda n: n["fecha"], reverse=True):
        clave = clave_titulo(n["titulo"])
        repetida = clave in claves_vistas
        claves_vistas.add(clave)
        n["importante"] = (not repetida) and es_importante(n, cfg, fuentes_alerta, patrones)
        if n["importante"] and not primera_vez:
            avisos.append(n)
    marcadas = {n["id"] for n in nuevas if n.get("importante")}
    for n in lista:
        if n["id"] in marcadas:
            n["importante"] = True

    avisados = set(estado.get("resumenes_avisados", []))
    resumenes_nuevos = [r for r in resumenes if r.get("id") and r["id"] not in avisados]

    h = hora_madrid().hour
    silencio = HORAS_SILENCIO[0] <= h < HORAS_SILENCIO[1]

    if primera_vez:
        telegram("✅ <b>Robot de noticias conectado.</b>\nA partir de ahora te aviso aquí de lo importante." + (f'\n<a href="{e(enlace_web())}">Abrir la web</a>' if WEB else ""))
        avisados.update(r["id"] for r in resumenes if r.get("id"))
    elif silencio:
        pendientes.extend({k: n[k] for k in ("titulo", "enlace", "seccion")} for n in avisos)
        pendientes = pendientes[-40:]
    else:
        if pendientes:
            telegram(mensaje_agrupado(pendientes, f"🌅 Mientras dormías ({len(pendientes)})", iconos))
            pendientes = []
        for n in avisos[:MAX_AVISOS_POR_RONDA]:
            telegram(mensaje_noticia(n, iconos), vista_previa=True)
        if len(avisos) > MAX_AVISOS_POR_RONDA:
            telegram(mensaje_agrupado(avisos[MAX_AVISOS_POR_RONDA:], "📌 Más noticias importantes", iconos))
        for r in resumenes_nuevos[:5]:
            if telegram(mensaje_resumen(r, iconos)) or not TOKEN:
                avisados.add(r["id"])

    # Al lanzarlo a mano con el botón "Run workflow", manda un mensaje de prueba
    if os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch":
        telegram(f"✅ <b>Radar del Podcast funcionando</b>\n{ok}/{len(fuentes)} fuentes leídas · {len(nuevas)} noticias nuevas · {len(avisos)} avisos.")

    # 4. Guardar todo
    escribir_json(NOTICIAS, lista, compacto=True)
    estado.update({
        "inicializado": True,
        "vistos": list(vistos)[-8000:],
        "claves": list(claves_vistas)[-8000:],
        "resumenes_avisados": list(avisados)[-3000:],
        "pendientes": pendientes,
    })
    escribir_json(ESTADO, estado, compacto=True)
    escribir_json(META, {
        "actualizado": ahora().isoformat(timespec="seconds"),
        "noticias": len(lista),
        "nuevas_en_esta_ronda": len(nuevas),
        "fuentes_ok": ok,
        "fuentes_fallidas": fallidas,
        "telegram": ("sin configurar" if not (TOKEN and CHAT) else
                     {"enviados": TG["enviados"], "errores": TG["errores"], "ultimo_error": TG["ultimo_error"]}),
    })
    print(f"Fuentes OK: {ok}/{len(fuentes)} · nuevas: {len(nuevas)} · avisos: {len(avisos)} · resúmenes nuevos: {len(resumenes_nuevos)}")
    for nombre, err in fallidas.items():
        print(f"  ✗ {nombre}: {err}")


if __name__ == "__main__":
    main()
