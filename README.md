# 📡 Radar del Podcast

Web de noticias para preparar el podcast. Se actualiza sola cada 10 minutos y avisa por Telegram de lo importante.

- **La web**: `index.html`, `estilos.css`, `app.js`.
- **Las fuentes**: `fuentes.json`. Ahí se añaden o se quitan webs.
- **El robot**: `scripts/actualizar.py`, que se lanza desde `.github/workflows/actualizar.yml`.
- **Los datos**: carpeta `data/`. El robot los rellena solo, no hay que tocarlos.
- **Los resúmenes con IA**: `data/resumenes.json`. Los escribe Claude siguiendo `RESUMENES.md`.
- **Los dossiers en Word**: carpeta `dossiers/`, que se lista en `data/dossiers.json`.

Las instrucciones de montaje están en la guía paso a paso (GUIA-PASO-A-PASO.docx).
