#!/usr/bin/env python3
"""
tiktok_pro.py - Pipeline FFmpeg para /tiktok-pro

  1. Detección de escenas (scdet, con respaldo select='gt(scene,..)')
  2. Ruptura de huella: micro-zoom alterno 1.04x-1.10x por escena + reencuadre,
     grano (noise=alls=4:allf=t+u), viñeta (vignette=PI/5), micro-EQ de audio
  3. Vibrant Cinema HDR: curves strong_contrast + eq + unsharp
  4. Exportación 1080x1920 @60fps, sin metadatos, libx264 crf17 slow, AAC 320k

La sincronía de la música se conserva: el audio no se corta, no se re-temporiza
ni cambia de tono/velocidad; el vídeo mantiene sus timestamps originales.

Uso:  python tiktok_pro.py <video> [--threshold 0.30] [--seed N] [--dry-run]
"""
import argparse
import json
import os
import random
import re
import shutil
import subprocess
import sys

OUT_W, OUT_H, FPS = 1080, 1920, 60
ZOOM_MIN, ZOOM_MAX = 1.04, 1.10
MIN_SCENE_SEC = 0.5          # ignora cortes demasiado juntos
MAX_SCENES = 400             # límite de seguridad para la expresión


def die(msg, code=1):
    print(f"[tiktok-pro] ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def check_tools():
    missing = [t for t in ("ffmpeg", "ffprobe") if shutil.which(t) is None]
    if missing:
        die(
            "No se encontró " + ", ".join(missing) + " en el PATH.\n"
            "  Windows: winget install --id Gyan.FFmpeg -e   (luego reabre la terminal)\n"
            "  macOS:   brew install ffmpeg\n"
            "  Linux:   sudo apt install ffmpeg"
        )


def probe(path):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", path],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        die(f"ffprobe no pudo leer el archivo:\n{r.stderr}")
    info = json.loads(r.stdout)
    v = next((s for s in info["streams"] if s.get("codec_type") == "video"), None)
    if v is None:
        die("El archivo no contiene pista de vídeo.")
    has_audio = any(s.get("codec_type") == "audio" for s in info["streams"])
    dur = float(info["format"].get("duration") or v.get("duration") or 0)
    return dur, has_audio


def detect_scenes(path, threshold):
    """Devuelve lista ordenada de tiempos (s) donde empieza una escena nueva."""
    times = []
    # scdet usa umbral 0-100; select usa 0-1.
    scd = f"scale=320:-2,scdet=threshold={threshold * 100:.1f}:sc_pass=0"
    r = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-an", "-sn",
         "-vf", scd, "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    times = [float(m) for m in re.findall(r"lavfi\.scd\.time:\s*([0-9.]+)", r.stderr)]
    if r.returncode != 0 or not times:
        # Respaldo universal
        sel = f"scale=320:-2,select='gt(scene\\,{threshold})',showinfo"
        r2 = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-an", "-sn",
             "-vf", sel, "-f", "null", "-"],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        alt = [float(m) for m in re.findall(r"pts_time:\s*([0-9.]+)", r2.stderr)]
        if alt or not times:
            times = alt
    cleaned, last = [], 0.0
    for t in sorted(set(times)):
        if t - last >= MIN_SCENE_SEC:
            cleaned.append(t)
            last = t
    return cleaned[:MAX_SCENES]


def scene_params(n, rng):
    """Zoom alterno (bajo/alto) y desplazamiento de reencuadre por escena."""
    params = []
    mid = (ZOOM_MIN + ZOOM_MAX) / 2
    for i in range(n):
        if i % 2 == 0:
            z = rng.uniform(ZOOM_MIN, mid)
        else:
            z = rng.uniform(mid, ZOOM_MAX)
        # fracción del margen disponible (-0.45..0.45 => no sale del encuadre)
        ox = rng.uniform(-0.45, 0.45)
        oy = rng.uniform(-0.35, 0.35)
        params.append((round(z, 4), round(ox, 3), round(oy, 3)))
    return params


def piecewise(frames, values):
    """if(lt(on,F1),v0,if(lt(on,F2),v1,...,vN))"""
    expr = f"{values[-1]}"
    for f, v in zip(reversed(frames), reversed(values[:-1])):
        expr = f"if(lt(on\\,{f})\\,{v}\\,{expr})"
    return expr


def build_video_filter(cuts, params):
    frames = [int(round(t * FPS)) for t in cuts]
    zs = [p[0] for p in params]
    oxs = [p[1] for p in params]
    oys = [p[2] for p in params]
    z_e = piecewise(frames, zs)
    ox_e = piecewise(frames, oxs)
    oy_e = piecewise(frames, oys)
    # x = centro + desplazamiento * margen libre (iw - iw/zoom)
    x_e = f"(iw-iw/zoom)/2+({ox_e})*(iw-iw/zoom)"
    y_e = f"(ih-ih/zoom)/2+({oy_e})*(ih-ih/zoom)"
    chain = [
        "setpts=PTS-STARTPTS",
        f"fps={FPS}",
        # cubrir 1080x1920 sin bandas negras
        f"scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase:flags=lanczos",
        f"crop={OUT_W}:{OUT_H}",
        "setsar=1",
        # micro-zoom por escena + reencuadre (1 frame de salida por frame de entrada)
        f"zoompan=z='{z_e}':x='{x_e}':y='{y_e}':d=1:s={OUT_W}x{OUT_H}:fps={FPS}",
        # Vibrant Cinema HDR
        "curves=preset=strong_contrast",
        "eq=contrast=1.12:saturation=1.22:brightness=0.02",
        "unsharp=5:5:0.8:5:5:0.0",
        # Grano + viñeta (cambian el hash de cada fotograma)
        "noise=alls=4:allf=t+u",
        "vignette=PI/5",
        "setsar=1",
        "format=yuv420p",
    ]
    return ",".join(chain)


# Micro-EQ imperceptible (±0.3-0.6 dB): no toca tempo ni tono.
AUDIO_FILTER = ",".join([
    "equalizer=f=90:t=q:w=1.1:g=0.4",
    "equalizer=f=420:t=q:w=1.4:g=-0.3",
    "equalizer=f=2700:t=q:w=1.2:g=0.5",
    "equalizer=f=9500:t=q:w=1.0:g=-0.4",
    "aresample=48000",
])


def main():
    ap = argparse.ArgumentParser(description="TikTok Pro pipeline (FFmpeg)")
    ap.add_argument("input")
    ap.add_argument("--threshold", type=float, default=0.30,
                    help="sensibilidad de corte 0-1 (default 0.30)")
    ap.add_argument("--seed", type=int, default=None,
                    help="semilla para zooms reproducibles")
    ap.add_argument("--output", default=None)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    check_tools()
    src = os.path.abspath(os.path.expanduser(a.input.strip('"')))
    if not os.path.isfile(src):
        die(f"No existe el archivo: {src}")
    base, _ = os.path.splitext(src)
    out = a.output or f"{base}_tiktok_pro.mp4"

    dur, has_audio = probe(src)
    print(f"[tiktok-pro] Entrada: {src}  ({dur:.2f}s, audio={'sí' if has_audio else 'no'})")

    print("[tiktok-pro] 1/4 Detectando escenas...")
    cuts = detect_scenes(src, a.threshold)
    print(f"[tiktok-pro]     {len(cuts) + 1} escenas; cortes en: "
          + (", ".join(f"{t:.2f}s" for t in cuts) or "ninguno"))

    rng = random.Random(a.seed)
    params = scene_params(len(cuts) + 1, rng)
    for i, (z, ox, oy) in enumerate(params):
        start = 0.0 if i == 0 else cuts[i - 1]
        print(f"[tiktok-pro]     escena {i + 1:>3} @ {start:7.2f}s  zoom {z:.3f}x  reencuadre ({ox:+.2f},{oy:+.2f})")

    vf = build_video_filter(cuts, params)
    cmd = ["ffmpeg", "-hide_banner", "-y", "-i", src,
           "-map", "0:v:0"]
    if has_audio:
        cmd += ["-map", "0:a:0", "-af", AUDIO_FILTER,
                "-c:a", "aac", "-b:a", "320k", "-ar", "48000"]
    cmd += [
        "-vf", vf,
        "-c:v", "libx264", "-crf", "17", "-preset", "slow",
        "-pix_fmt", "yuv420p", "-profile:v", "high",
        "-r", str(FPS),
        "-map_metadata", "-1", "-map_chapters", "-1",
        "-fflags", "+bitexact", "-flags:v", "+bitexact", "-flags:a", "+bitexact",
        "-metadata", "title=", "-metadata", "encoder=",
        "-movflags", "+faststart",
        out,
    ]
    print("[tiktok-pro] 2-4/4 Anti-copy + Vibrant Cinema HDR + exportación...")
    if a.dry_run:
        print(subprocess.list2cmdline(cmd))
        return
    r = subprocess.run(cmd)
    if r.returncode != 0:
        die("FFmpeg falló (ver salida arriba).", r.returncode)

    out_dur, _ = probe(out)
    print(f"[tiktok-pro] LISTO -> {out}")
    print(f"[tiktok-pro] Duración entrada {dur:.3f}s / salida {out_dur:.3f}s "
          f"(diferencia {abs(out_dur - dur) * 1000:.0f} ms)")


if __name__ == "__main__":
    main()
