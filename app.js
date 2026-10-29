// Radar del Podcast: lee los JSON de /data y pinta las noticias. Sin librerías.
(function () {
  'use strict';

  const ESPECIALES = [
    { id: 'destacadas', nombre: 'Destacadas', icono: '⭐' },
    { id: 'explicadas', nombre: 'Explicadas', icono: '🧠' },
  ];
  const DOSSIERS = { id: 'dossiers', nombre: 'Dossiers', icono: '📄' };
  const INTRO = {
    destacadas: 'Las noticias importantes de los últimos días (las mismas que te llegan a Telegram) y las que Claude ha explicado.',
    explicadas: 'Noticias explicadas por Claude: técnica, para tontos, por qué importa y de qué hablar en el podcast.',
    dossiers: 'Los dossiers completos en Word, para descargar.',
  };

  let secciones = [];
  let noticias = [];
  let resumenes = [];
  let dossiers = [];
  let actual = leer('pestana') || 'destacadas';
  let filtro = '';

  const $ = (id) => document.getElementById(id);

  function leer(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function guardar(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* sin almacenamiento */ } }

  // ---------- tema claro/oscuro
  const temaGuardado = leer('tema');
  if (temaGuardado) document.documentElement.setAttribute('data-theme', temaGuardado);
  $('tema').addEventListener('click', () => {
    const oscuro = document.documentElement.getAttribute('data-theme') === 'dark' ||
      (!document.documentElement.getAttribute('data-theme') && matchMedia('(prefers-color-scheme: dark)').matches);
    const nuevo = oscuro ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', nuevo);
    guardar('tema', nuevo);
  });

  // ---------- utilidades
  function esc(t) {
    return String(t == null ? '' : t).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }
  function negritas(t) { return esc(t).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>'); }
  function hace(fecha) {
    const d = new Date(fecha);
    if (isNaN(d)) return '';
    const min = Math.round((Date.now() - d) / 60000);
    if (min < 1) return 'ahora';
    if (min < 60) return `hace ${min} min`;
    const h = Math.round(min / 60);
    if (h < 24) return `hace ${h} h`;
    const dias = Math.round(h / 24);
    if (dias < 7) return `hace ${dias} d`;
    return d.toLocaleDateString('es-ES', { day: 'numeric', month: 'short' });
  }
  function seccion(id) { return secciones.find((s) => s.id === id) || { id, nombre: id, icono: '📰' }; }
  async function json(ruta, defecto) {
    try {
      const r = await fetch(`${ruta}?t=${Date.now()}`, { cache: 'no-store' });
      if (!r.ok) return defecto;
      return await r.json();
    } catch (e) { return defecto; }
  }
  function coincide(texto) {
    if (!filtro) return true;
    return texto.toLowerCase().includes(filtro);
  }

  // ---------- pintar
  function pintarPestanas() {
    const todas = [...ESPECIALES, ...secciones, DOSSIERS];
    $('pestanas').innerHTML = todas.map((s) => {
      let n = '';
      if (s.id === 'explicadas') n = resumenes.length;
      else if (s.id === 'destacadas') n = noticias.filter((x) => x.importante).length + resumenes.length;
      else if (s.id === 'dossiers') n = dossiers.length;
      else n = noticias.filter((x) => x.seccion === s.id).length;
      return `<button class="pestana" role="tab" data-id="${esc(s.id)}" aria-selected="${s.id === actual}">${s.icono} ${esc(s.nombre)}<span class="num">${n}</span></button>`;
    }).join('');
    $('pestanas').querySelectorAll('.pestana').forEach((b) => b.addEventListener('click', () => {
      actual = b.dataset.id;
      guardar('pestana', actual);
      history.replaceState(null, '', location.pathname);
      pintar();
      window.scrollTo({ top: 0 });
    }));
  }

  function tarjetaNoticia(n) {
    const s = seccion(n.seccion);
    const exp = resumenes.find((r) => r.id === n.id || r.noticia_id === n.id);
    return `<article class="tarjeta${n.importante ? ' destacada' : ''}" id="n-${esc(n.id)}">
      <div class="meta"><span class="etiqueta">${s.icono} ${esc(s.nombre)}</span><span>${esc(n.medio)}</span><span>${hace(n.fecha)}</span>${n.importante ? '<span>⭐ importante</span>' : ''}</div>
      <h2 class="titulo"><a href="${esc(n.enlace)}" target="_blank" rel="noopener">${esc(n.titulo)}</a></h2>
      ${n.resumen ? `<p class="resumen">${esc(n.resumen)}</p>` : ''}
      ${exp ? `<button class="boton-explicar" data-r="${esc(exp.id)}">🧠 Ver explicación</button>` : ''}
    </article>`;
  }

  function tarjetaResumen(r) {
    const s = seccion(r.seccion);
    const estado = (r.estado || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '');
    const bloque = (titulo, texto) => texto ? `<div class="bloque"><b>${titulo}</b> ${negritas(texto)}</div>` : '';
    const preguntas = (r.preguntas || []).length ? `<div class="bloque"><b>Preguntas para debatir:</b><ul class="preguntas">${r.preguntas.map((p) => `<li>${esc(p)}</li>`).join('')}</ul></div>` : '';
    const fuentes = (r.fuentes || []).map((f) => `<a href="${esc(f[1])}" target="_blank" rel="noopener">${esc(f[0])}</a>`).join(' · ');
    return `<article class="tarjeta destacada explicacion" id="r-${esc(r.id)}">
      <div class="meta"><span class="etiqueta">${s.icono} ${esc(s.nombre)}</span>${r.estado ? `<span class="etiqueta ${esc(estado)}">${esc(r.estado)}</span>` : ''}<span>${esc(r.fecha_evento || '')}</span><span>🧠 ${hace(r.fecha)}</span></div>
      <h3>${esc(r.titulo)}</h3>
      ${bloque('Qué ha pasado:', r.resumen)}
      ${bloque('Explicación técnica:', r.tecnica)}
      ${r.para_tontos ? `<div class="bloque tontos"><b>🧠 Para tontos:</b> ${negritas(r.para_tontos)}</div>` : ''}
      ${bloque('Por qué importa:', r.por_que_importa)}
      ${bloque('Contexto:', r.contexto)}
      ${bloque('Qué puede pasar ahora:', r.que_puede_pasar)}
      ${bloque('🎙️ Para el podcast:', r.de_que_hablar)}
      ${preguntas}
      ${fuentes ? `<div class="bloque fuentes"><b>Fuentes:</b> ${fuentes}</div>` : ''}
    </article>`;
  }

  function tarjetaDossier(d) {
    return `<article class="tarjeta">
      <div class="meta"><span class="etiqueta">📄 Dossier</span><span>${esc(d.fecha || '')}</span></div>
      <h2 class="titulo"><a href="${esc(d.archivo)}" download>${esc(d.titulo)}</a></h2>
      ${d.descripcion ? `<p class="resumen">${esc(d.descripcion)}</p>` : ''}
    </article>`;
  }

  function pintar() {
    pintarPestanas();
    $('intro').textContent = INTRO[actual] || '';
    let html = '';
    let total = 0;
    if (actual === 'explicadas') {
      const lista = resumenes.filter((r) => coincide(`${r.titulo} ${r.resumen || ''} ${r.para_tontos || ''}`));
      total = lista.length;
      html = lista.map(tarjetaResumen).join('');
    } else if (actual === 'dossiers') {
      total = dossiers.length;
      html = dossiers.map(tarjetaDossier).join('');
    } else if (actual === 'destacadas') {
      const rs = resumenes.filter((r) => coincide(`${r.titulo} ${r.resumen || ''}`)).slice(0, 10);
      const ns = noticias.filter((n) => n.importante && coincide(`${n.titulo} ${n.medio}`));
      total = rs.length + ns.length;
      html = rs.map(tarjetaResumen).join('') + ns.slice(0, 150).map(tarjetaNoticia).join('');
    } else {
      const ns = noticias.filter((n) => n.seccion === actual && coincide(`${n.titulo} ${n.medio} ${n.resumen || ''}`));
      const rs = resumenes.filter((r) => r.seccion === actual && coincide(r.titulo)).slice(0, 5);
      total = ns.length + rs.length;
      html = rs.map(tarjetaResumen).join('') + ns.slice(0, 200).map(tarjetaNoticia).join('');
    }
    $('lista').innerHTML = html;
    $('vacio').hidden = total > 0;
    $('contador').textContent = total ? `${total} resultados` : '';
    $('lista').querySelectorAll('.boton-explicar').forEach((b) => b.addEventListener('click', () => irA('r-' + b.dataset.r)));
  }

  function irA(ancla) {
    const id = ancla.replace(/^#/, '');
    if (id.startsWith('r-')) actual = 'explicadas';
    else if (id.startsWith('n-')) {
      const n = noticias.find((x) => 'n-' + x.id === id);
      if (n) actual = n.seccion;
    }
    filtro = '';
    $('buscar').value = '';
    pintar();
    const el = document.getElementById(id);
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'start' });
      el.classList.add('resaltar');
      setTimeout(() => el.classList.remove('resaltar'), 2500);
    }
  }

  function pintarEstado(meta) {
    if (!meta || !meta.actualizado) { $('estado').textContent = 'Aún no se ha ejecutado el robot.'; return; }
    const d = new Date(meta.actualizado);
    $('estado').textContent = `Actualizado ${hace(meta.actualizado)} (${d.toLocaleString('es-ES')}) · ${meta.noticias} noticias · ${meta.fuentes_ok} fuentes leídas`;
    const fallos = Object.entries(meta.fuentes_fallidas || {});
    $('fallos').hidden = fallos.length === 0;
    $('fallos-lista').innerHTML = fallos.map(([n, e]) => `<li><b>${esc(n)}</b>: ${esc(e)}</li>`).join('');
  }

  async function cargar(primera) {
    const [cfg, ns, rs, meta, ds] = await Promise.all([
      json('fuentes.json', {}), json('data/noticias.json', []), json('data/resumenes.json', []),
      json('data/meta.json', null), json('data/dossiers.json', []),
    ]);
    secciones = cfg.secciones || [];
    noticias = Array.isArray(ns) ? ns : [];
    resumenes = (Array.isArray(rs) ? rs : []).slice().sort((a, b) => String(b.fecha).localeCompare(String(a.fecha)));
    dossiers = Array.isArray(ds) ? ds : [];
    if (![...ESPECIALES, ...secciones, DOSSIERS].some((s) => s.id === actual)) actual = 'destacadas';
    pintarEstado(meta);
    if (primera && location.hash) irA(location.hash);
    else pintar();
  }

  $('buscar').addEventListener('input', (ev) => { filtro = ev.target.value.trim().toLowerCase(); pintar(); });
  window.addEventListener('hashchange', () => irA(location.hash));
  cargar(true);
  setInterval(() => { if (!document.hidden && !filtro) cargar(false); }, 5 * 60 * 1000);
})();
