#!/usr/bin/env python3
"""
Robot que actualiza la web de noticias del podcast.

Lo ejecuta GitHub Actions cada pocos minutos (ver .github/workflows/actualizar.yml):
  1. Lee las fuentes de fuentes.json (RSS/Atom).
  2. Guarda las noticias nuevas en data/noticias.json (lo que muestra la web).
  3. Traduce y explica cada noticia con la IA gratuita de Google (Gemini), le pone
     un nivel (1-5) y un tema, y la manda a Telegram. Las de nivel 4-5 suenan y te mencionan.
  4. Avisa también de los resúmenes nuevos que Claude escriba en data/resumenes.json.

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
import time
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
MAX_MENSAJES_POR_RONDA = 40  # más de esto se agrupa en un solo mensaje
MENCION = "@hugoo20"         # se te menciona en las noticias importantes y urgentes
NIVEL_MENCION = 5            # solo las URGENTES te mencionan y suenan
# IA gratuita de Google (Gemini). La clave se saca gratis en https://aistudio.google.com/apikey
# y se guarda en GitHub como secreto GEMINI_API_KEY. Se prueban los modelos en orden.
IA_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
IA_MODELOS = ["gemini-3.5-flash-lite", "gemini-flash-lite-latest", "gemini-3.5-flash", "gemini-flash-latest"]
IA_POR_PETICION = 10         # noticias que se mandan juntas a la IA
IA_PETICIONES_POR_RONDA = 4
IA_PETICIONES_POR_DIA = 400  # muy por debajo del límite gratis de Gemini
HORAS_SILENCIO = (0, 8)      # de 00:00 a 08:00 (hora de Madrid) no se envía nada; se junta en un resumen
ZONA = "Europe/Madrid"

UA = "Mozilla/5.0 (compatible; PodcastNoticiasBot/1.0; +https://github.com)"
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
WEB = os.environ.get("SITE_URL", "").strip()
IA_CLAVE = os.environ.get("GEMINI_API_KEY", "").strip()


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


# ----------------------------------------------------------------- niveles y temas
NIVELES = {
    5: ("🟥🟥🟥🟥🟥", "URGENTE"),
    4: ("🟧🟧🟧🟧⬜", "IMPORTANTE"),
    3: ("🟨🟨🟨⬜⬜", "INTERESANTE"),
    2: ("🟩🟩⬜⬜⬜", "NORMAL"),
    1: ("⬜⬜⬜⬜⬜", "POCO RELEVANTE"),
}
TEMAS = {
    "inversiones": "💰 INVERSIONES",
    "politica": "🏛️ POLÍTICA",
    "emprendimiento": "🚀 EMPRENDIMIENTO",
    "ia": "🤖 IA",
    "economia": "📊 ECONOMÍA",
    "zaragoza": "📍 ZARAGOZA Y ESPAÑA",
    "cine": "🎬 CINE",
    "moda": "👟 MODA",
    "musica": "🎵 MÚSICA",
    "deportes": "⚽ DEPORTES",
    "videojuegos": "🎮 VIDEOJUEGOS",
    "ciencia": "🔭 CIENCIA",
    "estudios": "🎓 ESTUDIOS",
    "otros": "🌍 OTROS",
}
IA = {"peticiones": 0, "errores": 0, "ultimo_error": "", "traducidas": 0, "error_traductor": ""}

INSTRUCCIONES_IA = """Eres el editor de noticias de un podcast español de amigos jóvenes (Zaragoza). Recibes titulares (a menudo en inglés) con su sección y una pista de prioridad por palabras clave.
Para CADA noticia devuelve, en español de España, claro y sin tecnicismos:
- "titulo": el titular traducido y natural (máx. 120 caracteres).
- "que_pasa": 1-2 frases explicando la noticia como a alguien que no sabe del tema. No inventes datos que no estén en el titular o el texto; si falta info, dilo con prudencia ("según el titular...").
- "por_que_importa": 1 frase con la consecuencia práctica (dinero, mercados, empleo, su vida, el podcast).
- "tema": uno de inversiones, politica, emprendimiento, ia, economia, zaragoza, cine, moda, musica, deportes, videojuegos, ciencia, estudios, otros. Trump, gobiernos, guerras y leyes = politica (salvo que sea claramente de mercados = inversiones).
- "nivel" de 1 a 5:
  5 URGENTE: SOLO estos casos, y nada más:
    a) Caídas grandes y repentinas (desplome, crash, peor día en meses/años) de acciones de empresas muy conocidas, de índices, de criptomonedas (Bitcoin, Ethereum...) o de ETFs. Las subidas NO son urgentes.
    b) Reuniones, anuncios, discursos, órdenes ejecutivas o aranceles del propio Donald Trump (lo que él dice o decide). Cotilleos, rumores sobre su salud o noticias sobre su entorno NO son urgentes (máximo 3).
    c) Salidas a bolsa (OPV/IPO, S-1) de empresas MUY grandes y conocidas por cualquiera (tipo Anthropic, OpenAI, SpaceX, Stripe, Shein, Revolut...). Si es una empresa que un joven español no conoce, NO es urgente.
  4 IMPORTANTE: todo lo demás que sea relevante: compras de empresas, rondas grandes, resultados de grandes empresas, subidas récord, Fed/BCE, lanzamientos e incidentes de IA, leyes en España, eventos grandes en Zaragoza, etc.
  3 INTERESANTE: buen tema de conversación para el podcast pero sin urgencia.
  2 NORMAL: noticia corriente.
  1 POCO RELEVANTE: local de otro país, nicho, promocional, opinión sin noticia o refrito.
Sé muy exigente: el 5 es raro (pocas al día) y si dudas, pon 4. Las notas de prensa de bufetes, despachos o empresas pequeñas no pasan de 2.
Responde SOLO con JSON: {"noticias": [{"id": "...", "titulo": "...", "que_pasa": "...", "por_que_importa": "...", "tema": "...", "nivel": 3}]}"""


def preguntar_ia(lote, nombres):
    entrada = [{
        "id": n["id"],
        "titular": n["titulo"],
        "texto": (n.get("resumen") or "")[:300],
        "medio": n.get("medio", ""),
        "seccion": nombres.get(n["seccion"], n["seccion"]),
        "pista_prioridad": ["normal", "importante", "urgente"][n.get("prioridad", 0)],
        "palabras_clave": n.get("motivos", [])[:4],
    } for n in lote]
    cuerpo = json.dumps({
        "model": IA_MODELOS[0],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": INSTRUCCIONES_IA},
            {"role": "user", "content": json.dumps(entrada, ensure_ascii=False)},
        ],
    }).encode()
    errores = []
    for modelo in IA_MODELOS:
        url = IA_URL
        cuerpo_json = json.loads(cuerpo)
        cuerpo_json["model"] = modelo
        req = urllib.request.Request(url, data=json.dumps(cuerpo_json).encode(), headers={
            "Authorization": f"Bearer {IA_CLAVE}",
            "Content-Type": "application/json",
        })
        try:
            resp = urllib.request.urlopen(req, timeout=120)
            bruto = resp.read().decode("utf-8", "replace")
            if resp.geturl() != url:
                bruto = f"[redirigido a {resp.geturl()} · {resp.status}] " + bruto
        except Exception as err:
            detalle = err.read().decode("utf-8", "replace")[:200] if hasattr(err, "read") else ""
            errores.append(f"{modelo}: {err} {detalle}")
            continue
        try:
            contenido = json.loads(bruto)["choices"][0]["message"]["content"] or ""
            # Se queda con lo que hay entre la primera { y la última }
            contenido = contenido[contenido.find("{"):contenido.rfind("}") + 1]
            return {x.get("id"): x for x in json.loads(contenido, strict=False).get("noticias", [])}
        except Exception:
            errores.append(f"{modelo}: respuesta rara: {bruto[:150]}")
    raise ValueError(" | ".join(errores))


def traducir(texto):
    # Traductor gratuito de Google (sin clave). Se usa si la IA no está disponible.
    url = "https://translate.googleapis.com/translate_a/single?" + urllib.parse.urlencode(
        {"client": "gtx", "sl": "auto", "tl": "es", "dt": "t", "q": texto})
    try:
        datos = json.loads(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=15).read())
        if datos[2] == "es":
            return texto
        return "".join(trozo[0] for trozo in datos[0] if trozo and trozo[0])
    except Exception:
        # Plan B: MyMemory (gratis, unos 5.000 caracteres al día)
        if re.search(r"[áéíóúñ¿¡]", texto):
            return texto
        url = "https://api.mymemory.translated.net/get?" + urllib.parse.urlencode({"q": texto[:450], "langpair": "en|es"})
        datos = json.loads(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=15).read())
        if datos.get("responseStatus") != 200:
            raise ValueError(datos.get("responseDetails", "MyMemory sin respuesta"))
        return html.unescape(datos["responseData"]["translatedText"])


def enriquecer(noticias, nombres, estado):
    """Traduce y explica las noticias nuevas y les pone nivel (1-5) y tema."""
    hoy = hora_madrid().strftime("%Y-%m-%d")
    uso = estado.get("ia_uso", {})
    if uso.get("dia") != hoy:
        uso = {"dia": hoy, "peticiones": 0}
    pendientes = list(noticias)
    # Primero lo que tiene pinta de importante, por si no da para todo
    pendientes.sort(key=lambda n: n.get("prioridad", 0), reverse=True)
    sin_ia = []
    for i in range(0, len(pendientes), IA_POR_PETICION):
        lote = pendientes[i:i + IA_POR_PETICION]
        if not IA_CLAVE or IA["peticiones"] >= IA_PETICIONES_POR_RONDA or uso["peticiones"] >= IA_PETICIONES_POR_DIA:
            sin_ia.extend(lote)
            continue
        try:
            IA["peticiones"] += 1
            uso["peticiones"] += 1
            res = preguntar_ia(lote, nombres)
        except Exception as err:
            IA["errores"] += 1
            detalle = err.read().decode("utf-8", "replace")[:300] if hasattr(err, "read") else ""
            IA["ultimo_error"] = f"{type(err).__name__}: {err} {detalle}"[:500]
            print("Error de la IA:", err, detalle)
            sin_ia.extend(lote)
            continue
        for n in lote:
            r = res.get(n["id"])
            if not r:
                sin_ia.append(n)
                continue
            n["titulo_es"] = (r.get("titulo") or "").strip()[:200]
            n["que_pasa"] = (r.get("que_pasa") or "").strip()[:500]
            n["por_que_importa"] = (r.get("por_que_importa") or "").strip()[:400]
            if r.get("tema") in TEMAS:
                n["tema"] = r["tema"]
            try:
                n["nivel"] = max(1, min(5, int(r.get("nivel"))))
            except Exception:
                pass
    for n in sin_ia[:80]:
        # Sin IA: se traduce el titular y el nivel sale de las palabras clave
        try:
            n["titulo_es"] = traducir(titular_limpio(n))
            IA["traducidas"] += 1
        except Exception as err:
            IA["error_traductor"] = f"{type(err).__name__}: {err}"[:200]
            break
    for n in noticias:
        # Sin IA no se marca nada como urgente: las palabras clave solo llegan a "importante"
        n.setdefault("nivel", 4 if n.get("prioridad", 0) >= 1 else 2)
        n.setdefault("tema", n["seccion"] if n["seccion"] in TEMAS else "otros")
        # Las palabras clave fuertes nunca bajan de "importante"
        if n.get("prioridad", 0) == 2:
            n["nivel"] = max(n["nivel"], 4)
    estado["ia_uso"] = uso


# ----------------------------------------------------------------- Telegram
TG = {"enviados": 0, "errores": 0, "ultimo_error": ""}


def telegram(texto, vista_previa=False, silencioso=False):
    if not TOKEN or not CHAT:
        return False
    datos = urllib.parse.urlencode({
        "chat_id": CHAT,
        "text": texto[:4000],
        "parse_mode": "HTML",
        "disable_web_page_preview": "false" if vista_previa else "true",
        "disable_notification": "true" if silencioso else "false",
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


def hora_de(n):
    f = fecha_de(n.get("fecha", ""))
    if not f:
        return ""
    try:
        from zoneinfo import ZoneInfo
        local = f.astimezone(ZoneInfo(ZONA))
    except Exception:
        local = f + dt.timedelta(hours=1)
    minutos = int((ahora() - f).total_seconds() // 60)
    if minutos < 60:
        hace = f"hace {max(minutos, 1)} min"
    elif minutos < 24 * 60:
        hace = f"hace {minutos // 60} h"
    else:
        hace = f"hace {minutos // 1440} d"
    return f"{local:%H:%M} ({hace})"


def titular_limpio(n):
    # Google News añade " - Medio" al final del titular
    t = n["titulo"]
    medio = n.get("medio") or ""
    if medio and t.endswith(" - " + medio):
        t = t[: -len(medio) - 3]
    return re.sub(r"\s+-\s+[^-]{2,40}$", "", t) if "news.google" in n.get("enlace", "") else t


def mensaje_noticia(n, avisar=True):
    barra, etiqueta = NIVELES.get(n.get("nivel", 2), NIVELES[2])
    cabecera = f"{barra} <b>{etiqueta}</b>"
    if avisar and n.get("nivel", 0) >= NIVEL_MENCION and MENCION:
        cabecera += f" · {MENCION}"
    lineas = [cabecera, f"<b>{e(TEMAS.get(n.get('tema'), '🌍 OTROS'))}</b>", "", f"<b>{e(n.get('titulo_es') or titular_limpio(n))}</b>"]
    if n.get("que_pasa"):
        lineas += ["", f"📝 <b>Qué pasa:</b> {e(n['que_pasa'])}"]
    if n.get("por_que_importa"):
        lineas.append(f"❗ <b>Por qué importa:</b> {e(n['por_que_importa'])}")
    pie = f"📰 {e(n.get('medio') or n.get('fuente', ''))}" + (f" · 🕒 {hora_de(n)}" if hora_de(n) else "")
    enlaces = f'🔗 <a href="{e(n["enlace"])}">Leer</a>'
    if WEB:
        enlaces += f' · 🌐 <a href="{e(enlace_web("n-" + n["id"]))}">Web</a>'
    lineas += ["", pie, enlaces]
    return "\n".join(lineas)


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
    lineas = [f"<b>{e(titulo)}</b>", ""]
    por_seccion = {}
    for n in lista[:15]:
        por_seccion.setdefault(n.get("seccion", ""), []).append(n)
    puestas = 0
    for sec, items in por_seccion.items():
        ic = iconos.get(sec, "📰")
        for n in items:
            marca = NIVELES.get(n.get("nivel", 2), NIVELES[2])[0][:1] + " "
            ic = TEMAS.get(n.get("tema"), ic).split(" ")[0]
            titulo = n.get("titulo_es") or (titular_limpio(n) if "titulo" in n else "")
            linea = f'{marca}{ic} <a href="{e(n.get("enlace", ""))}">{e(titulo)}</a>'
            # Telegram corta a 4096 caracteres: si no cabe, se deja para la web
            if len("\n".join(lineas)) + len(linea) > 3500:
                break
            lineas.append(linea)
            puestas += 1
    if len(lista) > puestas:
        lineas.append(f"\n…y {len(lista) - puestas} más en la web.")
    if WEB:
        lineas.append(f'\n🌐 <a href="{e(enlace_web())}">Abrir la web</a>')
    return "\n".join(lineas)


# ----------------------------------------------------------------- principal
def prioridad(n, cfg, fuentes_alerta, patrones, urgentes):
    """0 = normal, 1 = importante, 2 = urgente. Guarda en n["motivos"] por qué salta."""
    t = n["titulo"].lower()
    motivos = [pal for pal, p in patrones if p.search(t)]
    motivos_urg = [pal for pal, p in urgentes if p.search(t)]
    todos = list(dict.fromkeys(motivos_urg + motivos))
    # Quita repeticiones del tipo "acquire" dentro de "agrees to acquire"
    n["motivos"] = [m for m in todos if not any(m != o and m in o for o in todos)]
    en_seccion = n["seccion"] in cfg.get("secciones_con_alertas", [])
    de_fuente = n["fuente"] in fuentes_alerta
    if de_fuente:
        n["motivos"].insert(0, "fuente vigilada")
    if not (de_fuente or (en_seccion and motivos) or (en_seccion and motivos_urg)):
        return 0
    # Urgente: una palabra urgente + otra señal (fuente vigilada u otra palabra distinta)
    if motivos_urg and (de_fuente or len(n["motivos"]) >= 2):
        return 2
    return 1


def compilar(palabras):
    patrones = []
    for p in palabras:
        p = p.lower()
        if re.fullmatch(r"[\wáéíóúñü ]+", p):
            patrones.append((p, re.compile(r"\b" + re.escape(p) + r"\b")))
        else:
            patrones.append((p, re.compile(re.escape(p))))
    return patrones


def main():
    cfg = leer_json(FUENTES, {})
    fuentes = cfg.get("fuentes", [])
    iconos = {s["id"]: s.get("icono", "") for s in cfg.get("secciones", [])}
    fuentes_alerta = {f["nombre"] for f in fuentes if f.get("alerta")}
    patrones = compilar(cfg.get("palabras_alerta", []))
    urgentes = compilar(cfg.get("palabras_urgentes", []))
    nombres = {x["id"]: x.get("nombre", x["id"]) for x in cfg.get("secciones", [])}

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

    # 3. Clasificar las noticias nuevas (quitando las repetidas en varios medios)
    unicas = []
    for n in sorted(nuevas, key=lambda n: n["fecha"], reverse=True):
        clave = clave_titulo(n["titulo"])
        repetida = clave in claves_vistas
        claves_vistas.add(clave)
        n["prioridad"] = 0 if repetida else prioridad(n, cfg, fuentes_alerta, patrones, urgentes)
        if not repetida:
            unicas.append(n)
    if not primera_vez:
        enriquecer(unicas, nombres, estado)
    for n in unicas:
        n["importante"] = n.get("nivel", 0) >= 4  # destacadas en la web
    datos_nuevos = {n["id"]: n for n in unicas}
    for n in lista:
        if n["id"] in datos_nuevos:
            n.update({k: v for k, v in datos_nuevos[n["id"]].items()
                      if k in ("titulo_es", "que_pasa", "por_que_importa", "nivel", "tema", "importante", "motivos")})
    # De menos a más importante: lo importante queda abajo del todo, que es lo que ves primero
    avisos = sorted(unicas, key=lambda n: (n.get("nivel", 2), n["fecha"]))

    avisados = set(estado.get("resumenes_avisados", []))
    resumenes_nuevos = [r for r in resumenes if r.get("id") and r["id"] not in avisados]

    h = hora_madrid().hour
    noche = HORAS_SILENCIO[0] <= h < HORAS_SILENCIO[1]

    if primera_vez:
        telegram("✅ <b>Robot de noticias conectado.</b>\nA partir de ahora te aviso aquí de lo importante." + (f'\n<a href="{e(enlace_web())}">Abrir la web</a>' if WEB else ""))
        avisados.update(r["id"] for r in resumenes if r.get("id"))
    else:
        if not noche and pendientes:
            telegram(mensaje_agrupado(pendientes, f"🌅 Lo importante mientras dormías ({len(pendientes)}) · {MENCION}", iconos))
            pendientes = []
        # Todas las noticias llegan, sin sonido. Las importantes (nivel 4-5) suenan y te mencionan,
        # salvo de noche: entonces llegan en silencio y por la mañana te mando la lista.
        extra = avisos[:-MAX_MENSAJES_POR_RONDA] if len(avisos) > MAX_MENSAJES_POR_RONDA else []
        sueltas = avisos[-MAX_MENSAJES_POR_RONDA:]
        if extra:
            telegram(mensaje_agrupado(extra, f"🗞️ Otras {len(extra)} noticias", iconos), silencioso=True)
        for n in sueltas:
            importante = n.get("nivel", 0) >= NIVEL_MENCION
            telegram(mensaje_noticia(n, avisar=importante and not noche),
                     vista_previa=importante, silencioso=not importante or noche)
            if importante and noche:
                pendientes.append({k: n.get(k) for k in ("titulo", "titulo_es", "enlace", "seccion", "tema", "nivel", "medio")})
            time.sleep(1.1)  # Telegram no deja más de ~1 mensaje por segundo
        pendientes = pendientes[-40:]
        for r in resumenes_nuevos[:5]:
            if telegram(mensaje_resumen(r, iconos)) or not TOKEN:
                avisados.add(r["id"])

    # Al lanzarlo a mano con el botón "Run workflow", manda un mensaje de prueba
    if os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch":
        telegram(f"✅ <b>Radar del Podcast funcionando</b>\n{ok}/{len(fuentes)} fuentes leídas · {len(nuevas)} noticias nuevas · {sum(1 for n in avisos if n.get('nivel', 0) >= NIVEL_MENCION)} urgentes.")

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
        "ia": dict(IA, clave="sí" if IA_CLAVE else "falta GEMINI_API_KEY"),
        "telegram": ("sin configurar" if not (TOKEN and CHAT) else
                     {"enviados": TG["enviados"], "errores": TG["errores"], "ultimo_error": TG["ultimo_error"]}),
    })
    print(f"Fuentes OK: {ok}/{len(fuentes)} · nuevas: {len(nuevas)} · avisos: {len(avisos)} · resúmenes nuevos: {len(resumenes_nuevos)}")
    for nombre, err in fallidas.items():
        print(f"  ✗ {nombre}: {err}")


if __name__ == "__main__":
    main()
