# VIDEOS-TIKTOK
Skills para tiktok 

## /tiktok-pro

Skill global de Claude Code (carpeta `tiktok-pro/`).

**Instalar en Windows** (PowerShell, desde la carpeta del repo):

```powershell
cd tiktok-pro
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

Copia `SKILL.md` y `tiktok_pro.py` a `%USERPROFILE%\.claude\skills\tiktok-pro\` y
verifica/instala FFmpeg (`winget install --id Gyan.FFmpeg -e`) y Python.

**Usar:** en Claude Code, `/tiktok-pro C:/ruta/video.mp4` → genera `video_tiktok_pro.mp4`.
