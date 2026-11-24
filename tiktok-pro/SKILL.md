---
name: tiktok-pro
description: Procesa un vídeo para TikTok con FFmpeg - detecta escenas, aplica micro-zooms alternos por escena (1.04x-1.10x) con reencuadre, grano cinematográfico, viñeta y micro-EQ de audio (anti-copy / ruptura de huella), estética Vibrant Cinema HDR (contraste, saturación, nitidez) y exporta 1080x1920 a 60fps sin metadatos como [nombre]_tiktok_pro.mp4, manteniendo la sincronía de la música. Úsala cuando el usuario escriba /tiktok-pro [archivo_video] o pida preparar/optimizar un vídeo para TikTok.
argument-hint: "[archivo_video]"
allowed-tools: Bash(ffmpeg*), Bash(ffprobe*), Bash(python*)
---

# /tiktok-pro

Archivo de entrada: `$ARGUMENTS`

Ejecuta automáticamente, sin pedir confirmación, el pipeline incluido en esta skill
(`tiktok_pro.py`, en la misma carpeta que este SKILL.md).

## Pasos

1. Si `$ARGUMENTS` está vacío, pide al usuario la ruta del vídeo y detente.
2. Comprueba FFmpeg con `ffmpeg -version`. Si falla, indica el comando de instalación
   (Windows: `winget install --id Gyan.FFmpeg -e`, después reabrir la terminal) y detente.
3. Ejecuta el pipeline (pon la ruta del vídeo entre comillas; en Windows usa barras `/`
   o comillas para rutas con espacios):

   ```bash
   python "$HOME/.claude/skills/tiktok-pro/tiktok_pro.py" "$ARGUMENTS"
   ```

   Si `python` no existe, reintenta con `python3`. Usa un timeout largo (hasta 600000 ms):
   la codificación `-preset slow` a 60 fps tarda varias veces la duración del vídeo.
   Opciones: `--threshold 0.30` (sensibilidad de cortes, 0-1; bájalo si detecta pocas
   escenas), `--seed N` (zooms reproducibles), `--dry-run` (solo muestra el comando).
4. Informa al usuario: nº de escenas y zooms aplicados, ruta del archivo
   `[nombre]_tiktok_pro.mp4` y la diferencia de duración audio/vídeo que imprime el script.

## Qué hace el script (cadena FFmpeg)

1. **Detección de escenas:** `scdet` (respaldo `select='gt(scene,0.30)'`), cortes a menos
   de 0.5 s se fusionan.
2. **Anti-copy:** `zoompan` con zoom constante por escena alternando bajo/alto entre
   1.04x y 1.10x + reencuadre aleatorio dentro del margen; `noise=alls=4:allf=t+u`;
   `vignette=PI/5`; micro-EQ de audio (4 bandas `equalizer` de ±0.3-0.6 dB) sin
   cambiar tempo ni tono.
3. **Vibrant Cinema HDR:** `curves=preset=strong_contrast`,
   `eq=contrast=1.12:saturation=1.22:brightness=0.02`, `unsharp=5:5:0.8:5:5:0.0`.
4. **Exportación:** escala/recorte a 1080x1920 (sin bandas), `fps=60`,
   `-map_metadata -1 -fflags +bitexact`, `-c:v libx264 -crf 17 -preset slow
   -pix_fmt yuv420p -c:a aac -b:a 320k`, `+faststart`.

**Sincronía:** el audio nunca se corta ni se re-temporiza y el vídeo conserva sus
timestamps (solo se duplican frames para llegar a 60 fps), así que la música queda
exactamente alineada.
