#!/usr/bin/env python3
"""
director.py - Modo director de /tiktok-pro

Convierte un clip en una pieza con aporte propio: intro con título, clip sin
letterbox (fondo desenfocado), rótulos por escena, punch-zoom + destello,
narrador (voz neural edge-tts), cierre con llamada a comentar, grading por
escena, diseño sonoro y mezcla con ducking. Exporta 1080x1920 a 60 fps.

Flujo en dos pasos (Claude decide la parte creativa entre ambos):

  1) python director.py analyze <video>
       -> <video>_tiktok_pro_work/analysis.json  (recorte, escenas, luminancia)
       -> <video>_tiktok_pro_work/sheet.png      (hoja de contactos con tiempos)
       -> <video>_tiktok_pro_work/plan.json      (plantilla a completar)

  2) python director.py render <video> [--plan RUTA]
       -> <video>_tiktok_pro.mp4

plan.json (todos los tiempos de escenas/rótulos en segundos del clip original):
{
  "cuts": [2.86, 5.07, ...],               # opcional: sustituye a los detectados
  "intro":  {"still_time": 1.5, "duration": 3.0, "focus_x": 0.6,
             "title": "30 AÑOS", "subtitle": "ESPERANDO ESTA FRASE",
             "remove_box": [x, y, w, h]},   # opcional: tapa texto incrustado (coords del recorte)
  "labels": [{"start": 0, "end": 2.86, "text": "CÍCLOPE. POR FIN.", "accent": false}],
  "punches": [{"time": 13.40, "zoom": 1.13, "hold": 0.7, "flash": true}],
  "outro":  {"still_time": 15.9, "duration": 3.6, "line1": "¿TÚ TAMBIÉN",
             "line2": "LO GRITASTE?", "cta": "TE LEO EN COMENTARIOS"},
  "narration": {"voice": "es-MX-JorgeNeural",
                "lines": [{"section": "intro", "at": 0.3, "text": "...", "rate": "-4%"},
                          {"section": "main",  "at": 10.45, "text": "..."},
                          {"section": "outro", "at": 0.15, "text": "..."}]}
}
"""
import argparse
import asyncio
import json
import os
import random
import re
import statistics
import subprocess
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tiktok_pro as tp  # noqa: E402

W, H, FPS = 1080, 1920, 60
RED = "0xE50914"
GOLD = "0xFFE14D"
FONT_URLS = {
    "Anton-Regular.ttf": "https://raw.githubusercontent.com/google/fonts/main/ofl/anton/Anton-Regular.ttf",
    "BebasNeue-Regular.ttf": "https://raw.githubusercontent.com/google/fonts/main/ofl/bebasneue/BebasNeue-Regular.ttf",
}
FALLBACK_FONTS = [
    "C:/Windows/Fonts/impact.ttf", "C:/Windows/Fonts/arialbd.ttf",
    "/System/Library/Fonts/Supplemental/Impact.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]
DEFAULT_VOICE = "es-MX-JorgeNeural"


def log(msg):
    print(f"[tiktok-pro] {msg}", flush=True)


def run(cmd, quiet=False):
    r = subprocess.run(cmd, capture_output=quiet, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        if quiet:
            print(r.stderr[-3000:], file=sys.stderr)
        tp.die("FFmpeg falló.", r.returncode)
    return r


def ffpath(p):
    """Ruta segura para opciones de filtros (fontfile/textfile), también en Windows."""
    return os.path.abspath(p).replace("\\", "/").replace(":", "\\:")


# ---------------------------------------------------------------- fuentes
def get_fonts():
    fdir = os.path.join(HERE, "fonts")
    os.makedirs(fdir, exist_ok=True)
    out = {}
    for name, url in FONT_URLS.items():
        p = os.path.join(fdir, name)
        if not os.path.isfile(p) or os.path.getsize(p) < 10000:
            try:
                urllib.request.urlretrieve(url, p)
            except Exception:
                p = None
        out[name] = p
    fb = next((f for f in FALLBACK_FONTS if os.path.isfile(f)), None)
    title = out["Anton-Regular.ttf"] or fb
    sub = out["BebasNeue-Regular.ttf"] or title
    if not title:
        tp.die("No hay fuente disponible para los rótulos.")
    return title, sub


# ---------------------------------------------------------------- análisis
def detect_crop(src):
    r = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", src, "-an", "-vf",
         "fps=2,cropdetect=limit=24:round=4:reset=1", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    crops = re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", r.stderr)
    if not crops:
        return None
    best = max(set(crops), key=crops.count)
    return tuple(int(v) for v in best)


def scene_scores(src, pre):
    r = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", src, "-an", "-vf",
         f"{pre}scale=320:-2,select='gte(scene,0)',metadata=print:key=lavfi.scene_score",
         "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    pts = [float(x) for x in re.findall(r"pts_time:([0-9.]+)", r.stderr)]
    sc = [float(x) for x in re.findall(r"scene_score=([0-9.]+)", r.stderr)]
    return list(zip(pts, sc))


def detect_cuts(scores, dur, base=0.25, ratio=1.8, win=0.4, min_gap=0.5):
    """Picos de cambio de escena relativos a su entorno (robusto a cámara en mano)."""
    cands = []
    for i, (t, s) in enumerate(scores):
        if s < base:
            continue
        neigh = [x for (tt, x) in scores if abs(tt - t) <= win and tt != t]
        med = statistics.median(neigh) if neigh else 0
        if s >= max(base, ratio * med) or s > 0.6:
            cands.append((t, s))
    cuts = []
    for t, s in sorted(cands):
        if t < min_gap or dur - t < min_gap:
            continue
        if cuts and t - cuts[-1][0] < min_gap:
            if s > cuts[-1][1]:
                cuts[-1] = (t, s)
            continue
        cuts.append((t, s))
    return [round(t, 3) for t, _ in cuts]


def analyze(src, work):
    dur, has_audio = tp.probe(src)
    crop = detect_crop(src)
    iw, ih = video_size(src)
    if crop and (crop[0] < iw * 0.97 or crop[1] < ih * 0.97):
        cw, ch, cx, cy = crop
    else:
        cw, ch, cx, cy = iw, ih, 0, 0
    pre = f"crop={cw}:{ch}:{cx}:{cy},"
    cuts = detect_cuts(scene_scores(src, pre), dur)
    tiers = tp.classify_scenes(cuts, dur, tp.measure_luma(src, dur, pre))
    bounds = [0.0] + cuts + [dur]
    scenes = [{"start": round(bounds[i], 3), "end": round(bounds[i + 1], 3),
               "grade": tiers[i][0], "luma": None if tiers[i][1] is None else round(tiers[i][1], 1)}
              for i in range(len(bounds) - 1)]
    layout = "blurfill" if cw / ch > 0.75 else "full"
    info = {"source": src, "duration": round(dur, 3), "has_audio": has_audio,
            "size": [iw, ih], "crop": [cw, ch, cx, cy], "layout": layout,
            "scenes": scenes}
    os.makedirs(work, exist_ok=True)
    json.dump(info, open(os.path.join(work, "analysis.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    contact_sheet(src, work, pre, scenes, dur)
    plan_path = os.path.join(work, "plan.json")
    if not os.path.isfile(plan_path):
        json.dump(template_plan(scenes, dur), open(plan_path, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
    log(f"Duración {dur:.2f}s | recorte {cw}x{ch}+{cx}+{cy} | layout {layout}")
    for i, s in enumerate(scenes):
        log(f"  escena {i + 1:>2}: {s['start']:6.2f}-{s['end']:6.2f}s  grade {s['grade']:<6} luma {s['luma']}")
    log(f"Análisis: {os.path.join(work, 'analysis.json')}")
    log(f"Hoja de contactos: {os.path.join(work, 'sheet.png')}")
    log(f"Plan a completar: {plan_path}")


def video_size(src):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=width,height", "-of", "csv=p=0", src],
                       capture_output=True, text=True)
    w, h = r.stdout.strip().split(",")[:2]
    return int(w), int(h)


def contact_sheet(src, work, pre, scenes, dur):
    times = []
    for s in scenes:
        n = 1 if s["end"] - s["start"] < 3 else 2
        for k in range(n):
            times.append(s["start"] + (s["end"] - s["start"]) * (k + 1) / (n + 1))
    times = times[:24]
    tdir = os.path.join(work, "thumbs")
    os.makedirs(tdir, exist_ok=True)
    for f in os.listdir(tdir):
        os.remove(os.path.join(tdir, f))
    font = next((f for f in FALLBACK_FONTS if os.path.isfile(f)), None)
    for i, t in enumerate(times):
        dt = (f",drawtext=fontfile='{ffpath(font)}':text='{t:.2f}s':fontcolor=yellow:"
              f"fontsize=20:x=5:y=5:box=1:boxcolor=black@0.6") if font else ""
        run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", src, "-frames:v", "1",
             "-vf", f"{pre}scale=320:-2{dt}", os.path.join(tdir, f"{i:02d}.png")], quiet=True)
    cols = min(6, len(times))
    rows = (len(times) + cols - 1) // cols
    run(["ffmpeg", "-v", "error", "-y", "-framerate", "1", "-i", os.path.join(tdir, "%02d.png"),
         "-vf", f"tile={cols}x{rows}:padding=4", "-frames:v", "1",
         os.path.join(work, "sheet.png")], quiet=True)


def template_plan(scenes, dur):
    return {
        "cuts": [s["start"] for s in scenes[1:]],
        "intro": {"still_time": round(min(1.5, dur / 3), 2), "duration": 3.0, "focus_x": 0.5,
                  "title": "TÍTULO", "subtitle": "SUBTÍTULO"},
        "labels": [{"start": s["start"], "end": s["end"], "text": "", "accent": False}
                   for s in scenes],
        "punches": [],
        "outro": {"still_time": round(dur - 0.3, 2), "duration": 3.6,
                  "line1": "¿TÚ TAMBIÉN", "line2": "LO SENTISTE?", "cta": "TE LEO EN COMENTARIOS"},
        "narration": {"voice": DEFAULT_VOICE, "lines": []},
    }


# ---------------------------------------------------------------- narrador
def synth_narration(plan, work):
    nar = plan.get("narration") or {}
    lines = [l for l in nar.get("lines", []) if l.get("text")]
    if not lines:
        return []
    try:
        import edge_tts
    except ImportError:
        log("AVISO: falta edge-tts (python -m pip install edge-tts). Se renderiza sin narrador.")
        return []
    voice = nar.get("voice", DEFAULT_VOICE)
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    out = []

    async def one(i, l):
        mp3 = os.path.join(work, f"vo{i}.mp3")
        kw = {"rate": l.get("rate", "+0%"), "pitch": l.get("pitch", "+0Hz")}
        if proxy:
            kw["proxy"] = proxy
        await edge_tts.Communicate(l["text"], voice, **kw).save(mp3)
        return mp3

    for i, l in enumerate(lines):
        try:
            mp3 = asyncio.run(one(i, l))
        except Exception as e:  # sin red, voz inválida, etc.
            log(f"AVISO: narración '{l['text'][:30]}...' falló ({e}). Se omite.")
            continue
        wav = os.path.join(work, f"vo{i}.wav")
        run(["ffmpeg", "-v", "error", "-y", "-i", mp3, "-af",
             "silenceremove=start_periods=1:start_threshold=-45dB,areverse,"
             "silenceremove=start_periods=1:start_threshold=-45dB,areverse",
             "-ar", "48000", "-ac", "2", wav], quiet=True)
        out.append({**l, "wav": wav, "dur": audio_dur(wav)})
    return out


def audio_dur(p):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", p], capture_output=True, text=True)
    return float(r.stdout.strip() or 0)


# ---------------------------------------------------------------- render
def fade_alpha(a, b, fi=0.18, fo=0.15):
    return f"if(lt(t,{a:.3f}+{fi}),(t-{a:.3f})/{fi},if(gt(t,{b:.3f}-{fo}),({b:.3f}-t)/{fo},1))"


class Gfx:
    def __init__(self, work, title_font, sub_font):
        self.work, self.tf, self.sf, self.n = work, title_font, sub_font, 0

    def text(self, s, a, b, y, size, font=None, color="white"):
        self.n += 1
        p = os.path.join(self.work, f"t{self.n}.txt")
        open(p, "w", encoding="utf-8").write(s)
        f = font or self.tf
        return (f"drawtext=fontfile='{ffpath(f)}':textfile='{ffpath(p)}':fontsize={size}:"
                f"fontcolor={color}:x=(w-text_w)/2:y={y}:shadowcolor=black@0.85:shadowx=0:"
                f"shadowy=6:borderw=3:bordercolor=black@0.6:"
                f"alpha='{fade_alpha(a, b)}':enable='between(t,{a:.3f},{b:.3f})'")

    @staticmethod
    def bar(a, b, y, w=180, h=9):
        return (f"drawbox=x=(iw-{w})/2:y={y}:w={w}:h={h}:color={RED}@1:t=fill:"
                f"enable='between(t,{a + 0.12:.3f},{b - 0.1:.3f})'")


def piecewise_frames(bps, vals):
    """bps: frames donde cambia el valor (len = len(vals)-1)."""
    expr = str(vals[-1])
    for f, v in zip(reversed(bps), reversed(vals[:-1])):
        expr = f"if(lt(on,{f}),{v},{expr})"
    return expr


def render(src, work, plan_path, seed=None, out=None):
    info = json.load(open(os.path.join(work, "analysis.json"), encoding="utf-8"))
    plan = json.load(open(plan_path, encoding="utf-8"))
    dur = info["duration"]
    cw, ch, cx, cy = info["crop"]
    layout = info["layout"]
    title_font, sub_font = get_fonts()
    g = Gfx(work, title_font, sub_font)
    rng = random.Random(seed)
    base = os.path.splitext(src)[0]
    out = out or f"{base}_tiktok_pro.mp4"

    # ---- escenas, grading, zoom
    cuts = sorted(plan.get("cuts") or [s["start"] for s in info["scenes"][1:]])
    tiers = tp.classify_scenes(cuts, dur, tp.measure_luma(src, dur, f"crop={cw}:{ch}:{cx}:{cy},"))
    params = tp.scene_params(len(cuts) + 1, rng)
    segs = []  # (start, end, zoom, ox, oy, tier)
    b = [0.0] + cuts + [dur]
    for i in range(len(b) - 1):
        z, ox, oy = params[i]
        segs.append([b[i], b[i + 1], z, ox, oy, tiers[i][0]])
    for p in plan.get("punches", []):  # punch-zoom: parte la escena
        t, hold = p["time"], p.get("hold", 0.7)
        for i, s in enumerate(segs):
            if s[0] <= t < s[1]:
                end = min(s[1], t + hold)
                new = [[s[0], t] + s[2:], [t, end, p.get("zoom", 1.13), s[3], s[4], s[5]]]
                if end < s[1]:
                    new.append([end, s[1]] + s[2:])
                segs[i:i + 1] = [x for x in new if x[1] - x[0] > 0.01]
                break
    bps = [int(round(s[0] * FPS)) for s in segs[1:]]
    z_e = piecewise_frames(bps, [s[2] for s in segs])
    ox_e = piecewise_frames(bps, [s[3] for s in segs])
    oy_e = piecewise_frames(bps, [s[4] for s in segs])
    grade = []
    single = len({s[5] for s in segs}) == 1
    for tier in ("dark", "mid", "bright", "hot"):
        iv = "+".join(f"between(t,{s[0]:.3f},{s[1] - 0.001:.3f})" for s in segs if s[5] == tier)
        if not iv:
            continue
        for key in ("curves", "eq"):
            f = tp.GRADES[tier][key]
            grade.append(f if single else f"{f}:enable='{iv}'")

    # ---- geometría
    if layout == "blurfill":
        fw = W
        fh = int(round(W * ch / cw / 2) * 2)
        fy = max(380, (H - fh) // 2 - 60)
        label_y = fy - 150
    else:
        fw, fh, fy = W, H, 0
        label_y = 260
    cover_w = int(round(cw * H / ch / 2) * 2)  # ancho al escalar el recorte a 1920 de alto

    # ---- rótulos del clip
    labs = []
    for l in plan.get("labels", []):
        if not l.get("text"):
            continue
        a, e = max(0.05, l["start"]), min(dur - 0.05, l["end"])
        acc = l.get("accent", False)
        labs.append(g.text(l["text"], a, e, label_y, 96 if acc else 80,
                           color=GOLD if acc else "white"))
        labs.append(g.bar(a, e, label_y + 110))
    flashes = [p["time"] for p in plan.get("punches", []) if p.get("flash", True)]
    flash_e = "+".join(f"between(t,{t - 0.02:.3f},{t + 0.14:.3f})*0.25*(1-(t-{t - 0.02:.3f})/0.16)"
                       for t in flashes) or "0"

    crop_f = f"crop={cw}:{ch}:{cx}:{cy}"
    fg_chain = (f"zoompan=z='{z_e}':x='(iw-iw/zoom)/2+({ox_e})*(iw-iw/zoom)':"
                f"y='(ih-ih/zoom)/2+({oy_e})*(ih-ih/zoom)':d=1:s={fw}x{fh}:fps={FPS},"
                + ",".join(grade) + ",unsharp=5:5:0.8:5:5:0.0,setsar=1")
    if layout == "blurfill":
        main = (f"[0:v]setpts=PTS-STARTPTS,{crop_f},hqdn3d=1.2:1.2:2.5:2.5,fps={FPS},"
                f"trim=duration={dur},setpts=PTS-STARTPTS,split[m1][m2];"
                f"[m2]scale={cover_w}:{H},crop={W}:{H},gblur=sigma=38,"
                f"eq=brightness=-0.14:saturation=1.35,setsar=1[bg];"
                f"[m1]scale={fw}:{fh}:flags=lanczos,{fg_chain}[fg];"
                f"[bg][fg]overlay=0:{fy}:shortest=1,"
                f"drawbox=x=0:y={fy - 3}:w=iw:h=3:color={RED}@0.9:t=fill,"
                f"drawbox=x=0:y={fy + fh}:w=iw:h=3:color={RED}@0.9:t=fill,")
    else:
        main = (f"[0:v]setpts=PTS-STARTPTS,{crop_f},hqdn3d=1.2:1.2:2.5:2.5,fps={FPS},"
                f"trim=duration={dur},setpts=PTS-STARTPTS,"
                f"scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,crop={W}:{H},"
                f"{fg_chain},")
    main += (f"eq=brightness='{flash_e}':eval=frame,"
             + "".join(x + "," for x in labs)
             + "noise=alls=4:allf=t+u,vignette=PI/5,"
             f"fade=t=in:st=0:d=0.12:color=white,format=yuv420p,setsar=1[main];")

    # ---- intro / outro (fotogramas fijos)
    intro, outro = plan.get("intro") or {}, plan.get("outro") or {}
    ID, OD = float(intro.get("duration", 0) or 0), float(outro.get("duration", 0) or 0)
    inputs = ["-i", src]
    parts, idx = [], 1
    if ID > 0:
        rb = intro.get("remove_box")
        dl = f",delogo=x={rb[0]}:y={rb[1]}:w={rb[2]}:h={rb[3]}" if rb else ""
        png = os.path.join(work, "intro.png")
        run(["ffmpeg", "-v", "error", "-y", "-ss", str(intro.get("still_time", 1.0)), "-i", src,
             "-frames:v", "1", "-vf", f"{crop_f}{dl}", png], quiet=True)
        inputs += ["-loop", "1", "-framerate", str(FPS), "-t", str(ID), "-i", png]
        fx = float(intro.get("focus_x", 0.5))
        x0 = int(max(0, min(cover_w - W, fx * cover_w - W / 2)))
        nfr = int(ID * FPS)
        t1, t2 = intro.get("title", ""), intro.get("subtitle", "")
        txt = []
        if t1:
            txt.append(g.text(t1, 0.35, ID, 1120, 230))
        if t2:
            txt += [g.bar(0.75, ID, 1405, 260, 10), g.text(t2, 0.85, ID, 1445, 104, font=sub_font)]
        parts.append(
            f"[{idx}:v]scale={2 * cover_w}:{2 * H},crop={2 * W}:{2 * H}:{2 * x0}:0,"
            f"zoompan=z='1+0.07*on/{nfr}':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':"
            f"d={nfr}:s={W}x{H}:fps={FPS},trim=duration={ID},"
            f"{tp.GRADES['dark']['curves']},eq=contrast=1.10:saturation=1.20:brightness=0.04:gamma=1.15,"
            f"vignette=PI/5,drawbox=x=0:y=1080:w=iw:h=840:color=black@0.35:t=fill,"
            + "".join(x + "," for x in txt) +
            f"noise=alls=4:allf=t+u,fade=t=in:st=0:d=0.35,"
            f"fade=t=out:st={ID - 0.08:.3f}:d=0.08:color=white,format=yuv420p,setsar=1[intro];")
        idx += 1
    if OD > 0:
        png = os.path.join(work, "outro.png")
        st = min(float(outro.get("still_time", dur - 0.3)), dur - 0.05)
        run(["ffmpeg", "-v", "error", "-y", "-ss", str(st), "-i", src, "-frames:v", "1",
             "-vf", crop_f, png], quiet=True)
        inputs += ["-i", png]
        txt = []
        if outro.get("line1"):
            txt.append(g.text(outro["line1"], 0.25, OD, 690, 150))
        if outro.get("line2"):
            txt.append(g.text(outro["line2"], 0.55, OD, 860, 150))
        if outro.get("cta"):
            txt += [g.bar(0.9, OD, 1060, 260, 10),
                    g.text(outro["cta"], 1.1, OD, 1100, 86, font=sub_font, color=GOLD)]
        parts.append(
            f"[{idx}:v]scale={cover_w}:{H},crop={W}:{H}:(iw-{W})/2:0,loop=loop=-1:size=1,fps={FPS},"
            f"trim=duration={OD},setpts=PTS-STARTPTS,gblur=sigma=14,"
            f"eq=brightness=-0.18:saturation=1.3,vignette=PI/4,"
            + "".join(x + "," for x in txt) +
            f"noise=alls=4:allf=t+u,fade=t=in:st=0:d=0.25,fade=t=out:st={OD - 0.45:.3f}:d=0.45,"
            f"format=yuv420p,setsar=1[outro];")
        idx += 1
    order = (["[intro]"] if ID > 0 else []) + ["[main]"] + (["[outro]"] if OD > 0 else [])
    vcat = "".join(order) + f"concat=n={len(order)}:v=1:a=0[vout];"
    TOTAL = ID + dur + OD

    # ---- narración
    log("Generando narración...")
    vos = synth_narration(plan, work)
    off = {"intro": 0.0, "main": ID, "outro": ID + dur}
    vo_labels = []
    for v in vos:
        start = off.get(v.get("section", "main"), ID) + float(v.get("at", 0))
        if start + v["dur"] > TOTAL:
            log(f"AVISO: la línea '{v['text'][:30]}...' se sale del final; se recorta.")
        inputs += ["-i", v["wav"]]
        vo_labels.append((idx, start))
        log(f"  voz @ {start:6.2f}s ({v['dur']:.2f}s): {v['text']}")
        idx += 1

    # ---- audio
    ms = lambda s: int(round(s * 1000))
    a = ""
    if info["has_audio"]:
        a += (f"[0:a]aresample=48000,{tp.AUDIO_FILTER.replace(',aresample=48000', '')},"
              f"afade=t=out:st={max(0, dur - 0.5):.3f}:d=0.5,adelay={ms(ID)}|{ms(ID)},"
              f"apad=whole_dur={TOTAL}[mus];")
    else:
        a += f"anullsrc=r=48000:cl=stereo,atrim=duration={TOTAL}[mus];"
    mix = ["[musd]" if vo_labels else "[mus]"]
    if vo_labels:
        for k, (i, st) in enumerate(vo_labels):
            a += f"[{i}:a]adelay={ms(st)}|{ms(st)}[n{k}];"
        a += ("".join(f"[n{k}]" for k in range(len(vo_labels)))
              + f"amix=inputs={len(vo_labels)}:normalize=0:duration=longest,"
              "highpass=f=85,equalizer=f=200:t=q:w=1:g=-2,equalizer=f=3200:t=q:w=1.2:g=3,"
              "acompressor=threshold=-20dB:ratio=3.5:attack=5:release=120:makeup=2,"
              f"aecho=0.8:0.6:40:0.12,apad=whole_dur={TOTAL},asplit[vo][sc];"
              "[mus][sc]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=350[musd];")
        mix.append("[vo]")
    if ID > 0:
        a += (f"aevalsrc='0.9*sin(2*PI*48*t)*exp(-2.6*t)+0.25*sin(2*PI*96*t)*exp(-4*t)':s=48000:d=2.5,"
              f"aformat=channel_layouts=stereo,apad=whole_dur={TOTAL}[boom];"
              f"aevalsrc='0.10*sin(2*PI*55*t)+0.07*sin(2*PI*82.5*t)':s=48000:d={ID},"
              f"afade=t=in:d=0.4,afade=t=out:st={max(0, ID - 0.5):.3f}:d=0.5,"
              f"aformat=channel_layouts=stereo,apad=whole_dur={TOTAL}[drone0];")
        mix += ["[boom]", "[drone0]"]
    if OD > 0:
        wst = ID + dur - 0.6
        a += (f"anoisesrc=d=0.7:c=pink:a=0.5:r=48000,bandpass=f=1800:w=1500,"
              f"afade=t=in:d=0.55:curve=exp,afade=t=out:st=0.55:d=0.15,aformat=channel_layouts=stereo,"
              f"adelay={ms(wst)}|{ms(wst)},apad=whole_dur={TOTAL}[whoosh];"
              f"aevalsrc='0.18*sin(2*PI*55*t)+0.12*sin(2*PI*82.5*t)+0.06*sin(2*PI*110*t)':s=48000:d={OD},"
              f"afade=t=in:d=0.6,afade=t=out:st={max(0, OD - 0.8):.3f}:d=0.8,aformat=channel_layouts=stereo,"
              f"adelay={ms(ID + dur)}|{ms(ID + dur)},apad=whole_dur={TOTAL}[drone];")
        mix += ["[whoosh]", "[drone]"]
    a += ("".join(mix) + f"amix=inputs={len(mix)}:normalize=0:duration=first,"
          f"atrim=duration={TOTAL},loudnorm=I=-14:TP=-1.2:LRA=9,aresample=48000[aout]")

    graph = main + "".join(parts) + vcat + a
    gpath = os.path.join(work, "graph.txt")
    open(gpath, "w", encoding="utf-8").write(graph)

    log(f"Renderizando {TOTAL:.2f}s (intro {ID}s + clip {dur:.2f}s + cierre {OD}s)...")
    cmd = (["ffmpeg", "-hide_banner", "-y", "-loglevel", "error", "-stats"] + inputs +
           ["-filter_complex_script", gpath, "-map", "[vout]", "-map", "[aout]",
            "-c:v", "libx264", "-crf", "17", "-preset", "slow", "-profile:v", "high",
            "-pix_fmt", "yuv420p", "-r", str(FPS),
            "-c:a", "aac", "-b:a", "320k", "-ar", "48000",
            "-map_metadata", "-1", "-map_chapters", "-1", "-fflags", "+bitexact",
            "-flags:v", "+bitexact", "-flags:a", "+bitexact", "-movflags", "+faststart", out])
    run(cmd)
    log(f"LISTO -> {out}")
    log(f"Duración final {audio_dur(out):.2f}s")


def main():
    ap = argparse.ArgumentParser(description="Modo director de /tiktok-pro")
    ap.add_argument("cmd", choices=["analyze", "render"])
    ap.add_argument("input")
    ap.add_argument("--plan", default=None)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--output", default=None)
    a = ap.parse_args()
    tp.check_tools()
    src = os.path.abspath(os.path.expanduser(a.input.strip('"')))
    if not os.path.isfile(src):
        tp.die(f"No existe el archivo: {src}")
    work = os.path.splitext(src)[0] + "_tiktok_pro_work"
    if a.cmd == "analyze":
        analyze(src, work)
    else:
        if not os.path.isfile(os.path.join(work, "analysis.json")):
            analyze(src, work)
        render(src, work, a.plan or os.path.join(work, "plan.json"), a.seed, a.output)


if __name__ == "__main__":
    main()
