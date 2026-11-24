# VIDEOS-TIKTOK
Skills para tiktok 

## /tiktok-pro

Skill global de Claude Code (carpeta `tiktok-pro/`).

**Instalar en Windows** (PowerShell, desde la carpeta del repo):

```powershell
cd tiktok-pro
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

Copia `SKILL.md`, `tiktok_pro.py`, `director.py` y las fuentes a `%USERPROFILE%\.claude\skills\tiktok-pro\` y
verifica/instala FFmpeg (`winget install --id Gyan.FFmpeg -e`), Python y `edge-tts`.

**Usar:** en Claude Code, `/tiktok-pro C:/ruta/video.mp4` → genera `video_tiktok_pro.mp4`.

Modo director (por defecto): Claude analiza el vídeo (`director.py analyze`), escribe un
`plan.json` con intro, rótulos por escena, punch-zoom, cierre y guion del narrador, y lo
renderiza (`director.py render`): clip sin letterbox con fondo desenfocado, grading por
escena, voz neural (edge-tts), diseño sonoro, ducking y −14 LUFS.

Solo filtros (sin intro/rótulos/narrador): `/tiktok-pro C:/ruta/video.mp4 --simple`.
