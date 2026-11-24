---
name: tiktok-pro
description: Editor/director de vídeos para TikTok con FFmpeg. Convierte un clip en una pieza con aporte propio - intro con título, clip sin barras negras (fondo desenfocado), rótulos por escena, punch-zoom con destello, narrador con voz neural, cierre que invita a comentar, grading cinematográfico adaptativo por escena, diseño sonoro y mezcla con ducking - y exporta 1080x1920 a 60fps sin metadatos como [nombre]_tiktok_pro.mp4, manteniendo la sincronía de la música. Úsala cuando el usuario escriba /tiktok-pro [archivo_video] o pida editar, mejorar o preparar un vídeo para TikTok. Con "--simple" solo aplica el procesado visual/sonoro sin intro, rótulos ni narrador.
argument-hint: "[archivo_video] [--simple]"
allowed-tools: Bash(ffmpeg*), Bash(ffprobe*), Bash(python*), Read, Write
---

# /tiktok-pro: modo director

Argumentos: `$ARGUMENTS`

Eres el director y el editor senior. Trabaja sin pedir confirmación y entrega el vídeo
terminado. Los scripts están en la carpeta de esta skill:
`SKILL_DIR="$HOME/.claude/skills/tiktok-pro"` (usa `python`; si no existe, `python3`).

## 0. Requisitos
- `ffmpeg -version`. Si falla, indica `winget install --id Gyan.FFmpeg -e` (y reabrir la terminal) y detente.
- Si el usuario pidió `--simple`, ejecuta solo
  `python "$SKILL_DIR/tiktok_pro.py" "<video>"` (timeout 600000) e informa. Fin.

## 1. Analizar
```bash
python "$SKILL_DIR/director.py" analyze "<video>"
```
Genera en `<video>_tiktok_pro_work/`: `analysis.json` (recorte del letterbox, layout,
escenas con grade/luma), `sheet.png` (hoja de contactos con tiempos) y `plan.json` (plantilla).

## 2. Dirigir: ver y decidir
1. Lee `analysis.json` y **mira `sheet.png`** con Read. Identifica qué pasa en cada escena,
   el momento cumbre y el tono (épico, nostálgico, gracioso).
2. Si hace falta precisión (dónde está un texto incrustado, el mejor fotograma para intro/cierre),
   extrae fotogramas concretos: `ffmpeg -v error -y -ss T -i "<video>" -frames:v 1 -vf "crop=W:H:X:Y" f.png` y míralos.
3. Escribe `plan.json` (Write) con criterio de editor:
   - `cuts`: corrige los cortes detectados (quita falsos por cámara en mano, añade los que falten).
   - `intro` (3 s): `still_time` = el plano más icónico; `focus_x` (0-1) centra el sujeto;
     `title` corto y potente (2-3 palabras, MAYÚSCULAS); `subtitle` que complete la idea;
     `remove_box` [x,y,w,h] en coordenadas del recorte si hay subtítulos/marcas incrustadas en ese plano.
   - `labels`: un rótulo propio por escena (máx. ~22 caracteres, MAYÚSCULAS) que cuente
     una historia/chiste con los clips; `accent: true` en el clímax (sale en dorado y más grande).
   - `punches`: en el golpe principal (disparo, grito, caída del beat): `zoom` 1.10-1.15, `flash: true`.
   - `outro` (3-4 s): pregunta que invite a comentar en dos líneas + `cta`.
   - `narration`: voz `es-MX-JorgeNeural` (otras: `es-CO-GonzaloNeural`, `es-ES-AlvaroNeural`,
     `es-MX-DaliaNeural`). Guion breve y natural, en primera persona, como un fan:
     línea de gancho en `intro` (at 0.3; puede seguir sobre los primeros segundos del clip),
     una línea de comentario en `main` sobre la escena más comentable, y la pregunta en `outro` (at 0.15).
     Ninguna línea debe tapar un diálogo importante del clip ni otra línea. `rate` "-5%".."+8%".
   - Nada de marcas, logos ni nombres de terceros en los rótulos salvo los personajes.

## 3. Renderizar
```bash
python "$SKILL_DIR/director.py" render "<video>"
```
(timeout 600000; `--seed N` para zooms reproducibles, `--output RUTA` opcional). El log muestra
cuándo entra cada línea de voz y su duración: si alguna se solapa o se sale, ajusta `plan.json` y repite.

## 4. Control de calidad (obligatorio)
Extrae 6-8 fotogramas del resultado (intro, cada rótulo, clímax, cierre), únelos con
`ffmpeg ... -vf tile=8x1` y míralos. Revisa: textos legibles y sin chocar con caras o con
texto incrustado, sombras no aplastadas, altas luces no quemadas, cierre legible. Corrige el
plan y vuelve a renderizar si algo falla.

## 5. Entregar
Informa: ruta de `[nombre]_tiktok_pro.mp4`, duración, estructura (intro/escenas/cierre),
guion del narrador y una **descripción para TikTok** (gancho en la 1ª línea, pregunta para
comentar, 6-9 hashtags). Recuerda al usuario activar la etiqueta **"Contenido generado por IA"**
al publicar (el narrador es voz sintética) y que el aporte propio (narración, rótulos,
estructura) es lo que TikTok valora frente al contenido no original. Sin garantías de alcance.

## Qué hace el render (FFmpeg)
- **Layout:** recorta el letterbox (cropdetect); si el contenido es horizontal usa
  *blur-fill* (clip a ancho completo sobre fondo desenfocado del mismo plano, marco rojo fino).
- **Movimiento:** zoom alterno por escena 1.04x-1.10x con reencuadre + punch-zoom en el clímax.
- **Color:** grading por escena según luminancia (dark: protege sombras; mid; bright:
  `curves=strong_contrast` + `eq=contrast=1.12:saturation=1.22:brightness=0.02`),
  `hqdn3d` suave + `unsharp=5:5:0.8:5:5:0.0`, grano `noise=alls=4:allf=t+u`, `vignette=PI/5`.
- **Sonido:** música original con micro-EQ (sin cambiar tempo ni tono), narrador con
  EQ/compresión, ducking automático, golpe grave en la intro, whoosh y drone en el cierre,
  normalización a −14 LUFS.
- **Exportación:** 1080x1920, 60 fps, libx264 CRF 17 `slow`, yuv420p, AAC 320k,
  `-map_metadata -1 -fflags +bitexact`, `+faststart`.
- **Sincronía:** la música entra exactamente al empezar el clip y conserva su timing.
