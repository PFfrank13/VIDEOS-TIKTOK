# Instala la skill /tiktok-pro en %USERPROFILE%\.claude\skills\tiktok-pro
# y verifica/instala FFmpeg, Python y edge-tts (voz del narrador).
# Uso (PowerShell, desde la carpeta tiktok-pro del repo):
#   powershell -ExecutionPolicy Bypass -File .\install.ps1

$ErrorActionPreference = "Stop"
$dest = Join-Path $env:USERPROFILE ".claude\skills\tiktok-pro"
New-Item -ItemType Directory -Force -Path (Join-Path $dest "fonts") | Out-Null
foreach ($f in "SKILL.md", "tiktok_pro.py", "director.py") {
    Copy-Item -Force (Join-Path $PSScriptRoot $f) $dest
}
Copy-Item -Force (Join-Path $PSScriptRoot "fonts\*") (Join-Path $dest "fonts")
Write-Host "Skill copiada en $dest" -ForegroundColor Green

if (Get-Command ffmpeg -ErrorAction SilentlyContinue) {
    Write-Host "FFmpeg ya instalado:" -ForegroundColor Green
    ffmpeg -hide_banner -version | Select-Object -First 1
} else {
    Write-Host "FFmpeg no encontrado. Instalando con winget..." -ForegroundColor Yellow
    winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements
    Write-Host "Cierra y vuelve a abrir la terminal (y Claude Code) para que FFmpeg entre en el PATH." -ForegroundColor Yellow
}

if (Get-Command python -ErrorAction SilentlyContinue) {
    python --version
} else {
    Write-Host "Python no encontrado. Instalando con winget..." -ForegroundColor Yellow
    winget install --id Python.Python.3.12 -e --accept-source-agreements --accept-package-agreements
    Write-Host "Reabre la terminal y vuelve a ejecutar este script para instalar edge-tts." -ForegroundColor Yellow
    exit
}

Write-Host "Instalando edge-tts (voz del narrador)..." -ForegroundColor Cyan
python -m pip install --upgrade edge-tts

Write-Host "`nListo. En Claude Code escribe:  /tiktok-pro C:/ruta/a/tu_video.mp4" -ForegroundColor Cyan
Write-Host "Solo filtros, sin intro/rotulos/narrador:  /tiktok-pro C:/ruta/video.mp4 --simple" -ForegroundColor Cyan
