#!/usr/bin/env python3
"""
KwaiBoost Cut Formatter
Layout: fundo 9:16 | titulo PNG (caixa branca) | video 4:3 CENTRALIZADO | data PNG
"""
import subprocess, sys, os, csv, json, re
from pathlib import Path
from datetime import datetime

try:
    from PIL import Image, ImageDraw, ImageFont
    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False

# ── Dimensoes ────────────────────────────────────────────────────────────────
OUT_W = 720
OUT_H = 1280

# Video 4:3, largura total, CENTRALIZADO verticalmente no fundo
VID_W = 720
VID_H = int(VID_W * 3 // 4)   # 540  (proporcao 4:3)
VID_X = 0
VID_Y = (OUT_H - VID_H) // 2  # 370  (centro exato)

# Titulo: area acima do video (0 ate VID_Y)
TITLE_MAX_W  = OUT_W - 20      # 700px max
TITLE_FONT   = 46
TITLE_MARGIN = 14              # margem do topo

# Data: area abaixo do video (VID_Y+VID_H ate OUT_H)
DATE_FONT    = 36
DATE_MARGIN  = 20              # distancia abaixo do video

# ── Codec ────────────────────────────────────────────────────────────────────
CODEC_PRESET = "fast"
CRF          = "20"
PROFILE      = "high"
LEVEL        = "4.1"
PIX_FMT      = "yuv420p"
FPS          = "30"
AUDIO_BR     = "128k"
AUDIO_SR     = "44100"

# ── Presets de originalidade ─────────────────────────────────────────────────
COLOR_PRESETS = [
    {"brightness": 0.04, "contrast": 1.06, "saturation": 1.10},
    {"brightness": 0.02, "contrast": 1.08, "saturation": 1.12},
    {"brightness": 0.06, "contrast": 1.04, "saturation": 1.08},
    {"brightness": 0.03, "contrast": 1.10, "saturation": 1.06},
    {"brightness": 0.05, "contrast": 1.05, "saturation": 1.14},
    {"brightness": 0.01, "contrast": 1.07, "saturation": 1.09},
    {"brightness": 0.04, "contrast": 1.09, "saturation": 1.11},
    {"brightness": 0.02, "contrast": 1.06, "saturation": 1.13},
]
SHARP_PRESETS = [
    (5, 5, 0.8), (3, 3, 0.6), (5, 5, 1.0), (7, 7, 0.7),
    (3, 3, 0.9), (5, 5, 0.5), (7, 7, 0.8), (3, 3, 1.0),
]
ATEMPO_VALUES = [0.98, 0.99, 1.00, 1.01, 1.02]

# ── FFmpeg ───────────────────────────────────────────────────────────────────
def _resolve_exe(name):
    import shutil
    script_dir = os.path.dirname(os.path.abspath(__file__))
    local = os.path.join(script_dir, name + ".exe")
    if os.path.isfile(local):
        return local
    found = shutil.which(name)
    return found if found else name

def check_ffmpeg():
    exe = _resolve_exe("ffmpeg")
    try:
        subprocess.run([exe, "-version"], capture_output=True, check=True)
        return True
    except Exception:
        return False

FFMPEG_EXE  = _resolve_exe("ffmpeg")
FFPROBE_EXE = _resolve_exe("ffprobe")

# ── Fonte ────────────────────────────────────────────────────────────────────
def _find_font():
    import glob
    # Linux system fonts (Railway/Docker)
    for path in [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf",
    ]:
        if os.path.isfile(path):
            return path
    # Windows fonts fallback
    script_dir = os.path.dirname(os.path.abspath(__file__))
    for name in ["Montserrat-Bold.ttf", "MontserratBold.ttf", "arialbd.ttf", "arial.ttf"]:
        p = os.path.join(script_dir, "fonts", name)
        if os.path.isfile(p):
            return p
    for name in ["arialbd.ttf", "arial.ttf", "calibrib.ttf"]:
        p = os.path.join(r"C:\Windows\Fonts", name)
        if os.path.isfile(p):
            return p
    return None

FONT_PATH = _find_font()

# ── Duracao ───────────────────────────────────────────────────────────────────
def get_duration(path):
    try:
        r = subprocess.run(
            [FFMPEG_EXE, "-i", str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
        m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", r.stderr)
        if m:
            return int(m.group(1))*3600 + int(m.group(2))*60 + float(m.group(3))
    except Exception:
        pass
    return 60.0

# ── Pillow: gera imagens PNG ──────────────────────────────────────────────────
def _pil_font(size):
    if not HAS_PILLOW:
        return None
    if FONT_PATH:
        try:
            return ImageFont.truetype(FONT_PATH, size)
        except Exception:
            pass
    try:
        return ImageFont.load_default(size=size)
    except Exception:
        return ImageFont.load_default()

CORES_TEXTO = {
    "branco":   "#FFFFFF",
    "preto":    "#111111",
    "amarelo":  "#FFD600",
    "vermelho": "#E63946",
    "azul":     "#2AABEE",
    "verde":    "#2CFFA7",
}

def gerar_titulo_img(titulo, out_path, max_w=TITLE_MAX_W, font_size=TITLE_FONT,
                     nicho="noticias", estilo="caixa_branca", cor_texto="branco"):
    """Caixa com bordas arredondadas, texto colorido, com suporte a múltiplos estilos."""
    if not HAS_PILLOW:
        return False, 0
    texto = titulo.upper().strip() or "-"
    font  = _pil_font(font_size)
    palavras = texto.split()
    linhas, atual = [], ""
    for p in palavras:
        teste = atual + (" " if atual else "") + p
        if font.getbbox(teste)[2] <= max_w - 60:
            atual = teste
        else:
            if atual:
                linhas.append(atual)
            atual = p
    if atual:
        linhas.append(atual)
    larg = min(max(font.getbbox(l)[2] for l in linhas) + 60, max_w)
    alt  = len(linhas) * (font_size + 14) + 40
    img  = Image.new("RGBA", (larg, alt), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    txt_color = CORES_TEXTO.get(cor_texto, "#FFFFFF")

    if estilo == "caixa_branca":
        draw.rounded_rectangle([(0,0),(larg-1,alt-1)], radius=22, fill=(255,255,255,245))
        txt_color = CORES_TEXTO.get(cor_texto, "#111111") if cor_texto != "branco" else "#111111"
    elif estilo == "caixa_preta":
        draw.rounded_rectangle([(0,0),(larg-1,alt-1)], radius=22, fill=(0,0,0,210))
        txt_color = CORES_TEXTO.get(cor_texto, "#FFFFFF") if cor_texto != "preto" else "#FFFFFF"
    elif estilo == "sombra":
        # sem caixa, só sombra no texto
        pass
    elif estilo == "sem_caixa":
        pass
    elif nicho == "musica":
        draw.rounded_rectangle([(0,0),(larg-1,alt-1)], radius=22, fill=(255,255,255,240))
        draw.rounded_rectangle([(0,0),(larg-1,alt-1)], radius=22, outline=(255,20,147,255), width=4)
        txt_color = CORES_TEXTO.get(cor_texto, "#6B0020") if cor_texto != "branco" else "#6B0020"

    y = 20
    for linha in linhas:
        w = font.getbbox(linha)[2]
        x = (larg-w)//2
        if estilo == "sombra":
            # Desenha sombra primeiro
            draw.text((x+2, y+2), linha, font=font, fill=(0,0,0,180))
        draw.text((x, y), linha, font=font, fill=txt_color)
        y += font_size + 14
    img.save(out_path)
    return True, alt

def gerar_data_img(data_str, out_path, font_size=DATE_FONT):
    """Caixinha branca para a data."""
    if not HAS_PILLOW:
        return False
    font = _pil_font(font_size)
    bbox = font.getbbox(data_str)
    tw   = bbox[2]
    th   = bbox[3]
    larg = tw + 48
    alt  = th + 24
    img  = Image.new("RGBA", (larg, alt), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([(0,0),(larg-1,alt-1)], radius=14, fill=(255,255,255,235))
    draw.text((24, 12), data_str, font=font, fill="black")
    img.save(out_path)
    return True

# ── Presets extras de fingerprint ────────────────────────────────────────────
CROP_OFFSETS    = [(0,0),(3,2),(5,4),(2,5),(4,1),(6,3),(1,6),(3,4)]
# ROTATE removido — causava artefatos visuais no overlay
GRAIN_SEEDS     = [12, 37, 58, 91, 24, 63, 45, 79]
GRAIN_STRENGTH  = 8
SPEED_PRESETS   = [0.98, 0.99, 1.00, 1.01, 1.02, 0.99, 1.01, 1.00]
AUDIO_EQ_PRESETS = [
    "bass=g=1.5", "bass=g=1.0", "bass=g=2.0", "treble=g=1.0",
    "bass=g=1.5,treble=g=0.5", "bass=g=0.5,treble=g=1.0",
    "bass=g=2.0,treble=g=0.5", "treble=g=1.5",
]
FADE_IN  = 0.4
FADE_OUT = 0.5

# ── Filtro de video ───────────────────────────────────────────────────────────
def build_video_filter(idx, mirror, duration=60.0, vid_w=None, vid_h=None):
    """Fingerprint: cor, nitidez, grain, velocidade, fade, mirror. Scale seguro sem zoom."""
    color = COLOR_PRESETS[idx % len(COLOR_PRESETS)]
    sharp = SHARP_PRESETS[idx % len(SHARP_PRESETS)]
    speed = SPEED_PRESETS[idx % len(SPEED_PRESETS)]

    w = vid_w if vid_w else VID_W
    h = vid_h if vid_h else VID_H

    fade_out_start = max(0.5, duration * speed - FADE_OUT - 0.1)

    F = []
    if speed != 1.0:
        F.append(f"setpts={1.0/speed:.4f}*PTS")
    # Zoom 5%: garante que nao sobra borda, crop centralizado
    zw = int(w * 1.30); zw = zw if zw%2==0 else zw+1
    zh = int(h * 1.30); zh = zh if zh%2==0 else zh+1
    F.append(f"scale={zw}:{zh}:flags=lanczos:force_original_aspect_ratio=increase")
    F.append(f"crop={w}:{h}")
    F.append(f"eq=brightness={color['brightness']:.3f}:contrast={color['contrast']:.3f}:saturation={color['saturation']:.3f}")
    F.append(f"unsharp=luma_msize_x={sharp[0]}:luma_msize_y={sharp[1]}:luma_amount={sharp[2]}")
    F.append(f"noise=alls={GRAIN_STRENGTH}:allf=t+u")
    if mirror:
        F.append("hflip")
    F.append(f"fade=t=in:st=0:d={FADE_IN}")
    F.append(f"fade=t=out:st={fade_out_start:.2f}:d={FADE_OUT}")
    return ",".join(F)

# ── Processa um video ─────────────────────────────────────────────────────────
def process_one(video_path, bg_path, title, output_path,
                idx=0, logo_path=None, date_str=None, mirror=True,
                nicho="noticias", estilo="caixa_branca", cor_texto="branco"):
    if not date_str:
        date_str = datetime.now().strftime("%d/%m/%y")

    import tempfile
    tmp_dir = tempfile.gettempdir()
    titulo_img = os.path.join(tmp_dir, f"_titulo_tmp_{idx}.png")
    data_img   = os.path.join(tmp_dir, f"_data_tmp_{idx}.png")

    ok_titulo, titulo_h = gerar_titulo_img(title, titulo_img, nicho=nicho,
                                            estilo=estilo, cor_texto=cor_texto)
    ok_data             = gerar_data_img(date_str, data_img)

    if not ok_titulo:
        return False, "Pillow nao disponivel. Instale: pip install Pillow"

    # ── Dimensoes por nicho ────────────────────────────────────────────────
    if nicho == "musica":
        # 1:1 — quadrado centralizado
        vid_w = OUT_W           # 720
        vid_h = OUT_W           # 720  (1:1)
        vid_x = 0
    else:
        # 4:3 — padrao noticias
        vid_w = VID_W           # 720
        vid_h = VID_H           # 540  (4:3)
        vid_x = VID_X           # 0

    # Video: CENTRALIZADO verticalmente (igual ao CapCut)
    vid_y = (OUT_H - vid_h) // 2

    # Titulo: centralizado na area ACIMA do video
    titulo_y = max(TITLE_MARGIN, (vid_y - titulo_h) // 2)

    # Data: logo abaixo do video (so noticias)
    data_y = vid_y + vid_h + DATE_MARGIN

    # ── Duracao (deve vir antes do filtro) ───────────────────────────────────
    duration = get_duration(video_path)
    atempo   = ATEMPO_VALUES[idx % len(ATEMPO_VALUES)]
    speed    = SPEED_PRESETS[idx % len(SPEED_PRESETS)]
    audio_eq = AUDIO_EQ_PRESETS[idx % len(AUDIO_EQ_PRESETS)]

    # ── Filtro ───────────────────────────────────────────────────────────────
    vf = build_video_filter(idx, mirror, duration=duration, vid_w=vid_w, vid_h=vid_h)

    # inputs: [0]=fundo  [1]=video  [2]=titulo  [3]=data
    if ok_data and nicho == "noticias":
        # Noticias: com data
        filtro = (
            f"[0:v]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,"
            f"crop={OUT_W}:{OUT_H}[bg];"
            f"[1:v]{vf}[vid];"
            f"[bg][vid]overlay={vid_x}:{vid_y}[t1];"
            f"[t1][2:v]overlay=(W-w)/2:{titulo_y}[t2];"
            f"[t2][3:v]overlay=(W-w)/2:{data_y}[outv]"
        )
        extra = ["-i", titulo_img, "-i", data_img]
    else:
        # Musica ou sem data
        filtro = (
            f"[0:v]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,"
            f"crop={OUT_W}:{OUT_H}[bg];"
            f"[1:v]{vf}[vid];"
            f"[bg][vid]overlay={vid_x}:{vid_y}[t1];"
            f"[t1][2:v]overlay=(W-w)/2:{titulo_y}[outv]"
        )
        extra = ["-i", titulo_img]

    cmd  = [FFMPEG_EXE, "-y", "-hide_banner", "-loglevel", "error"]
    cmd += ["-loop", "1", "-i", str(bg_path)]   # [0] fundo
    cmd += ["-i", str(video_path)]               # [1] video
    cmd += extra                                  # [2] titulo [3] data
    cmd += ["-filter_complex", filtro, "-map", "[outv]", "-map", "1:a?"]
    # Audio: atempo (velocidade) + EQ (equalização sutil)
    af_parts = []
    if speed != 1.0:
        af_parts.append(f"atempo={atempo}")
    af_parts.append(audio_eq)
    cmd += ["-af", ",".join(af_parts)]
    cmd += [
        "-c:v", "libx264", "-preset", CODEC_PRESET, "-crf", CRF,
        "-profile:v", PROFILE, "-level", LEVEL, "-pix_fmt", PIX_FMT,
        "-r", FPS, "-maxrate", "8000k", "-bufsize", "16000k",
        "-movflags", "+faststart",
        "-c:a", "aac", "-b:a", AUDIO_BR, "-ar", AUDIO_SR, "-ac", "2",
        "-map_metadata", "-1", "-map_chapters", "-1",
        "-shortest", "-t", str(duration), str(output_path)
    ]

    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=600)
        ok  = r.returncode == 0
        err = (r.stderr + r.stdout) if not ok else ""
    except subprocess.TimeoutExpired:
        ok, err = False, "Timeout"
    except Exception as e:
        ok, err = False, str(e)
    finally:
        for p in [titulo_img, data_img]:
            try:
                if os.path.exists(p): os.remove(p)
            except Exception:
                pass
    return ok, err


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--input", "-i", required=True)
    p.add_argument("--output", "-o", default="saida_kwai_cut")
    p.add_argument("--bg", "-b", required=True)
    p.add_argument("--csv", "-c", required=True)
    p.add_argument("--no-mirror", action="store_true")
    args = p.parse_args()
    if not check_ffmpeg():
        print("FFmpeg nao encontrado!"); sys.exit(1)
    if not HAS_PILLOW:
        print("Pillow nao instalado! pip install Pillow"); sys.exit(1)
    input_dir  = Path(args.input)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%d/%m/%y")
    with open(args.csv, encoding="utf-8") as f:
        entries = list(csv.DictReader(f))
    total = len(entries); success = 0
    for idx, row in enumerate(entries, 1):
        vp  = input_dir / row["arquivo"].strip()
        ttl = row["titulo"].strip()
        if not vp.exists():
            print(f"[{idx}/{total}] nao encontrado: {vp.name}"); continue
        out = output_dir / f"kwai_{vp.stem}.mp4"
        ok, err = process_one(vp, args.bg, ttl, out, idx=idx-1,
                              date_str=today, mirror=not args.no_mirror)
        if ok:
            print(f"[{idx}/{total}] OK {os.path.getsize(out)/1024/1024:.1f}MB")
            success += 1
        else:
            print(f"[{idx}/{total}] ERRO: {err}")
    print(f"Prontos: {success}/{total}")

if __name__ == "__main__":
    main()
