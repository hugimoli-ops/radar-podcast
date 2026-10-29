# Instrucciones para Claude: resúmenes de la web

Este archivo lo lee la tarea programada de Claude (la "rutina") cada vez que se ejecuta. Una persona no necesita tocarlo.

## Qué hacer en cada ejecución

1. Lee `data/noticias.json`. Fíjate en las noticias con `"importante": true` publicadas desde la última ejecución, y también en las demás de Inversiones, Emprendimiento e IA.
2. Elige un máximo de **5 historias** que merezcan un segmento del podcast. Para elegirlas, sigue los criterios de `/mnt/project-files/podcast/fuentes.md` y de la memoria del proyecto: Trump y su efecto en los mercados, salidas a bolsa, movimientos extremos, compras de empresas, modelos e incidentes de IA, y famosos que visitan Zaragoza.
   - No elijas noticias que ya estén en `data/resumenes.json`.
   - Si hay varias noticias sobre la misma historia, haz un solo resumen.
3. Investiga cada historia: abre la fuente original y busca otra fuente independiente. No inventes nada. Si algo no está confirmado, dilo en el campo `estado`.
4. Añade cada resumen **al principio** de `data/resumenes.json` y conserva como máximo 150 entradas.
5. Haz commit y push a `main`. El robot de GitHub verá los resúmenes nuevos, los publicará en la web y mandará cada uno a Telegram.

## Formato de cada resumen

Todo en español de España. Usa `**negrita**` para las cifras clave.

```json
{
  "id": "el id de la noticia en noticias.json (o uno nuevo, corto y único)",
  "noticia_id": "id de la noticia original, si existe",
  "titulo": "Titular claro, sin exagerar",
  "seccion": "inversiones | emprendimiento | ia | zaragoza | economia | cine | moda | musica | deportes | videojuegos | ciencia | estudios | otros",
  "fecha": "2026-10-23T14:00:00+02:00 (cuándo se escribe el resumen)",
  "fecha_evento": "cuándo pasó (texto libre)",
  "estado": "Confirmado | Declaración | Rumor | Previsión",
  "resumen": "Qué ha pasado, en 2-4 frases",
  "tecnica": "Explicación con tecnicismos y cifras",
  "para_tontos": "Lo mismo en palabras sencillas, sin perder matices",
  "por_que_importa": "Consecuencias",
  "contexto": "Antecedentes imprescindibles",
  "que_puede_pasar": "Próximos pasos, separando hechos de posibilidades",
  "de_que_hablar": "Por qué es buena historia para el podcast",
  "preguntas": ["2-4 preguntas para debatir"],
  "fuentes": [["Fuente original", "https://..."], ["Fuente independiente", "https://..."]]
}
```

Nunca recomiendes comprar ni vender. Si no hay nada que merezca la pena, no añadas nada.
