import os
import asyncio
import uuid
import subprocess
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError
import uvicorn

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

API_ID   = int(os.environ["TELEGRAM_API_ID"])
API_HASH = os.environ["TELEGRAM_API_HASH"]

pending_clients: dict = {}
active_clients:  dict = {}
corte_jobs:      dict = {}
download_jobs:   dict = {}  # progresso de downloads

LIMITE_GRATIS = 5  # usos por 24h para usuário comum

# ===================== BANCO DE DADOS =====================

DB_PATH = "/data/teleload.db"

import os as _os
_os.makedirs("/data", exist_ok=True)

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS usos (
            phone TEXT NOT NULL,
            timestamp TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS vip (
            phone TEXT PRIMARY KEY,
            expira TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS codigos (
            codigo TEXT PRIMARY KEY,
            usado INTEGER DEFAULT 0,
            usado_por TEXT,
            criado_em TEXT NOT NULL
        );
    """)
    conn.commit()
    conn.close()

init_db()

def is_vip(phone: str) -> bool:
    conn = get_db()
    row = conn.execute("SELECT expira FROM vip WHERE phone=?", (phone,)).fetchone()
    conn.close()
    if not row:
        return False
    return datetime.fromisoformat(row["expira"]) > datetime.utcnow()

def get_vip_expiry(phone: str):
    conn = get_db()
    row = conn.execute("SELECT expira FROM vip WHERE phone=?", (phone,)).fetchone()
    conn.close()
    if not row:
        return None
    expira = datetime.fromisoformat(row["expira"])
    if expira > datetime.utcnow():
        return expira.strftime("%d/%m/%Y")
    return None

def contar_usos(phone: str) -> int:
    limite = (datetime.utcnow() - timedelta(hours=24)).isoformat()
    conn = get_db()
    count = conn.execute(
        "SELECT COUNT(*) as c FROM usos WHERE phone=? AND timestamp>?",
        (phone, limite)
    ).fetchone()["c"]
    conn.close()
    return count

def registrar_uso(phone: str):
    conn = get_db()
    conn.execute("INSERT INTO usos (phone, timestamp) VALUES (?, ?)",
                 (phone, datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()

def verificar_limite(phone: str):
    """Lança HTTPException se usuário atingiu o limite."""
    if is_vip(phone):
        return  # VIP não tem limite
    usos = contar_usos(phone)
    if usos >= LIMITE_GRATIS:
        raise HTTPException(403, f"LIMITE_ATINGIDO|{usos}")

# ===================== MODELS =====================

class SendCodeRequest(BaseModel):
    phone: str

class VerifyCodeRequest(BaseModel):
    phone: str
    code: str
    phone_code_hash: str

class PasswordRequest(BaseModel):
    phone: str
    password: str

class ChannelsRequest(BaseModel):
    session: str

class FilesRequest(BaseModel):
    session: str
    channel_id: int

class DownloadRequest(BaseModel):
    session: str
    channel_id: int
    message_id: int
    phone: str

class CortarRequest(BaseModel):
    session: str
    channel_id: int
    message_id: int
    formato: str = "9:16"
    phone: str

class AtivarVipRequest(BaseModel):
    phone: str
    codigo: str

class StatusRequest(BaseModel):
    phone: str

class GerarCodigoRequest(BaseModel):
    senha: str
    quantidade: int = 1

ADMIN_SENHA = os.environ.get("ADMIN_SENHA", "teleload2024")

FORMATOS = {
    "9:16":  (608,  1080),
    "1:1":   (1080, 1080),
    "16:9":  (1080, 608),
}

# ===================== UTILS =====================

async def get_client(session: str):
    if session not in active_clients:
        client = TelegramClient(StringSession(session), API_ID, API_HASH)
        await client.connect()
        active_clients[session] = client
    return active_clients[session]

def fmt_size(size: int) -> str:
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"

# ===================== ROTAS =====================

@app.get("/")
async def health():
    return {"status": "ok"}


@app.post("/status")
async def get_status(body: StatusRequest):
    """Retorna status do usuário: usos restantes, se é VIP, quando expira."""
    vip = is_vip(body.phone)
    usos = contar_usos(body.phone)
    restantes = max(0, LIMITE_GRATIS - usos) if not vip else 999
    expira = get_vip_expiry(body.phone)
    return {
        "vip": vip,
        "usos_hoje": usos,
        "restantes": restantes,
        "limite": LIMITE_GRATIS,
        "expira": expira,
    }


@app.post("/ativar-vip")
async def ativar_vip(body: AtivarVipRequest):
    """Ativa VIP com código."""
    conn = get_db()
    row = conn.execute("SELECT * FROM codigos WHERE codigo=?", (body.codigo.upper(),)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(400, "Código inválido.")
    if row["usado"]:
        conn.close()
        raise HTTPException(400, "Código já utilizado.")

    expira = (datetime.utcnow() + timedelta(days=30)).isoformat()
    conn.execute("UPDATE codigos SET usado=1, usado_por=? WHERE codigo=?",
                 (body.phone, body.codigo.upper()))
    conn.execute("INSERT OR REPLACE INTO vip (phone, expira) VALUES (?, ?)",
                 (body.phone, expira))
    conn.commit()
    conn.close()
    return {"ok": True, "expira": datetime.fromisoformat(expira).strftime("%d/%m/%Y")}


@app.post("/admin/gerar-codigos")
async def gerar_codigos(body: GerarCodigoRequest):
    """Gera códigos VIP. Protegido por senha."""
    if body.senha != ADMIN_SENHA:
        raise HTTPException(403, "Senha incorreta.")
    conn = get_db()
    codigos = []
    for _ in range(min(body.quantidade, 50)):
        codigo = "VIP-" + uuid.uuid4().hex[:8].upper()
        conn.execute("INSERT INTO codigos (codigo, criado_em) VALUES (?, ?)",
                     (codigo, datetime.utcnow().isoformat()))
        codigos.append(codigo)
    conn.commit()
    conn.close()
    return {"codigos": codigos}


@app.post("/admin/listar-codigos")
async def listar_codigos(body: GerarCodigoRequest):
    """Lista todos os códigos."""
    if body.senha != ADMIN_SENHA:
        raise HTTPException(403, "Senha incorreta.")
    conn = get_db()
    rows = conn.execute("SELECT * FROM codigos ORDER BY criado_em DESC").fetchall()
    conn.close()
    return {"codigos": [dict(r) for r in rows]}


# ===================== AUTH =====================

@app.post("/auth/send-code")
async def send_code(body: SendCodeRequest):
    client = TelegramClient(StringSession(), API_ID, API_HASH)
    await client.connect()
    result = await client.send_code_request(body.phone)
    pending_clients[body.phone] = client
    return {"phone_code_hash": result.phone_code_hash}


@app.post("/auth/verify-code")
async def verify_code(body: VerifyCodeRequest):
    client = pending_clients.get(body.phone)
    if not client:
        raise HTTPException(400, "Sessão não encontrada.")
    try:
        await client.sign_in(phone=body.phone, code=body.code,
                             phone_code_hash=body.phone_code_hash)
    except PhoneCodeInvalidError:
        raise HTTPException(400, "Código inválido.")
    except SessionPasswordNeededError:
        return {"requires_2fa": True}
    session_string = client.session.save()
    active_clients[session_string] = client
    del pending_clients[body.phone]
    me = await client.get_me()
    return {"session": session_string,
            "user": {"name": f"{me.first_name or ''} {me.last_name or ''}".strip(),
                     "username": me.username, "phone": me.phone}}


@app.post("/auth/verify-2fa")
async def verify_2fa(body: PasswordRequest):
    client = pending_clients.get(body.phone)
    if not client:
        raise HTTPException(400, "Sessão não encontrada.")
    await client.sign_in(password=body.password)
    session_string = client.session.save()
    active_clients[session_string] = client
    del pending_clients[body.phone]
    me = await client.get_me()
    return {"session": session_string,
            "user": {"name": f"{me.first_name or ''} {me.last_name or ''}".strip(),
                     "username": me.username}}


# ===================== CANAIS / ARQUIVOS =====================

@app.post("/channels")
async def list_channels(body: ChannelsRequest):
    client = await get_client(body.session)
    dialogs = await client.get_dialogs()
    return {"channels": [
        {"id": d.id, "name": d.name,
         "type": "canal" if d.is_channel else "grupo",
         "unread": d.unread_count}
        for d in dialogs if d.is_channel or d.is_group
    ]}


@app.post("/files")
async def list_files(body: FilesRequest):
    client = await get_client(body.session)
    files = []
    async for msg in client.iter_messages(body.channel_id, limit=50):
        if msg.document:
            attrs = {type(a).__name__: a for a in msg.document.attributes}
            name = getattr(attrs.get("DocumentAttributeFilename"), "file_name",
                           f"arquivo_{msg.id}")
            files.append({"message_id": msg.id, "name": name,
                          "size": msg.document.size,
                          "size_fmt": fmt_size(msg.document.size),
                          "mime": msg.document.mime_type,
                          "date": msg.date.strftime("%d/%m/%Y")})
    return {"files": files}


@app.post("/download")
async def download_file(body: DownloadRequest):
    verificar_limite(body.phone)
    client = await get_client(body.session)
    msg = await client.get_messages(body.channel_id, ids=body.message_id)
    if not msg or not msg.document:
        raise HTTPException(404, "Arquivo não encontrado.")
    attrs = {type(a).__name__: a for a in msg.document.attributes}
    filename = getattr(attrs.get("DocumentAttributeFilename"), "file_name",
                       f"arquivo_{msg.id}")
    total_size = msg.document.size
    dl_id = str(uuid.uuid4())[:8]
    download_jobs[dl_id] = {"baixado": 0, "total": total_size, "status": "baixando"}
    registrar_uso(body.phone)

    async def file_stream():
        baixado = 0
        async for chunk in client.iter_download(msg.document, chunk_size=4*1024*1024):
            baixado += len(chunk)
            download_jobs[dl_id]["baixado"] = baixado
            if baixado >= total_size:
                download_jobs[dl_id]["status"] = "concluido"
            yield chunk

    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "X-Download-Id": dl_id,
        "X-File-Size": str(total_size),
        "Access-Control-Expose-Headers": "X-Download-Id, X-File-Size",
    }
    return StreamingResponse(file_stream(), media_type=msg.document.mime_type, headers=headers)


@app.get("/download/progresso/{dl_id}")
async def progresso_download(dl_id: str):
    job = download_jobs.get(dl_id)
    if not job:
        raise HTTPException(404, "Download não encontrado.")
    pct = int((job["baixado"] / job["total"] * 100)) if job["total"] > 0 else 0
    return {
        "baixado": job["baixado"],
        "total": job["total"],
        "pct": pct,
        "status": job["status"],
    }


@app.post("/cortar")
async def cortar_video(body: CortarRequest):
    verificar_limite(body.phone)
    registrar_uso(body.phone)
    job_id = str(uuid.uuid4())[:8]
    corte_jobs[job_id] = {"status": "iniciando", "progresso": 0,
                          "cenas": [], "erro": None, "log": ""}
    asyncio.create_task(_processar_corte(job_id, body))
    return {"job_id": job_id}


@app.get("/cortar/status/{job_id}")
async def status_corte(job_id: str):
    job = corte_jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job não encontrado.")
    return job


@app.get("/cortar/baixar/{job_id}/{cena_idx}")
async def baixar_cena(job_id: str, cena_idx: int):
    job = corte_jobs.get(job_id)
    if not job or job["status"] != "concluido":
        raise HTTPException(404, "Cena não disponível.")
    cenas = job.get("cenas", [])
    if cena_idx >= len(cenas):
        raise HTTPException(404, "Índice inválido.")
    arquivo = Path(cenas[cena_idx]["arquivo"])
    if not arquivo.exists():
        raise HTTPException(404, "Arquivo não encontrado.")
    return FileResponse(str(arquivo), media_type="video/mp4",
        filename=arquivo.name,
        headers={"Content-Disposition": f'attachment; filename="{arquivo.name}"'})


# ===================== PROCESSAMENTO DE CORTE =====================

async def _processar_corte(job_id: str, body: CortarRequest):
    tmp_dir = Path(f"/tmp/corte_{job_id}")
    tmp_dir.mkdir(parents=True, exist_ok=True)

    def prog(p, status=""):
        corte_jobs[job_id]["progresso"] = p
        if status:
            corte_jobs[job_id]["status"] = status

    def log(msg):
        corte_jobs[job_id]["log"] = msg

    try:
        prog(5, "baixando")
        log("Baixando vídeo do Telegram...")
        client = await get_client(body.session)
        msg = await client.get_messages(body.channel_id, ids=body.message_id)
        if not msg or not msg.document:
            raise RuntimeError("Arquivo não encontrado.")

        attrs = {type(a).__name__: a for a in msg.document.attributes}
        fname = getattr(attrs.get("DocumentAttributeFilename"), "file_name",
                        f"video_{body.message_id}.mp4")
        video_path = tmp_dir / fname

        with open(video_path, "wb") as f:
            async for chunk in client.iter_download(msg.document, chunk_size=4*1024*1024):
                f.write(chunk)

        prog(30, "analisando")
        log("Extraindo áudio para análise...")

        wav_path = tmp_dir / "audio.wav"
        subprocess.run([
            "ffmpeg", "-y", "-i", str(video_path),
            "-vn", "-acodec", "pcm_s16le", "-ar", "22050", "-ac", "1",
            str(wav_path)
        ], capture_output=True, check=True)

        r = subprocess.run(["ffmpeg", "-i", str(video_path)],
                           capture_output=True, text=True)
        import re
        m = re.search(r"Duration: (\d+):(\d+):(\d+\.?\d*)", r.stderr)
        duracao = (int(m.group(1))*3600 + int(m.group(2))*60 +
                   float(m.group(3))) if m else 0.0

        prog(40, "analisando")
        log("Analisando padrões de áudio com IA...")

        import numpy as np
        import librosa

        y, sr = librosa.load(str(wav_path), sr=22050, mono=True)
        hop = int(sr * 0.25)
        frame_len = int(sr * 0.5)

        rms   = librosa.feature.rms(y=y, frame_length=frame_len, hop_length=hop)[0]
        rms_n = (rms - rms.min()) / (rms.max() - rms.min() + 1e-9)

        try:
            f0, _, _ = librosa.pyin(y, fmin=80, fmax=400,
                                     frame_length=frame_len, hop_length=hop)
            pitch    = np.nan_to_num(f0, nan=0.0)
            pitch_n  = (pitch - pitch.min()) / (pitch.max() - pitch.min() + 1e-9)
            pitch_var = np.clip(np.abs(np.diff(pitch_n, prepend=pitch_n[0]))*3, 0, 1)
        except Exception:
            pitch_var = np.zeros(len(rms_n))

        onset   = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)
        onset_n = (onset - onset.min()) / (onset.max() - onset.min() + 1e-9)

        min_len   = min(len(rms_n), len(pitch_var), len(onset_n))
        rms_n     = rms_n[:min_len]
        pitch_var = pitch_var[:min_len]
        onset_n   = onset_n[:min_len]

        silencio      = rms_n < 0.15
        bonus         = np.zeros(min_len)
        for i in range(4, min_len):
            if silencio[i-4:i].any() and rms_n[i] > 0.6:
                bonus[max(0, i-2):i+8] = 0.4

        score = rms_n*0.40 + pitch_var*0.25 + onset_n*0.25 + bonus*0.10
        score = np.convolve(score, np.ones(8)/8, mode='same')
        score = (score - score.min()) / (score.max() - score.min() + 1e-9)
        times = librosa.frames_to_time(range(min_len), sr=sr, hop_length=hop)

        threshold = np.percentile(score, 65 if duracao>=1800 else 55 if duracao>=600 else 45)
        ativo     = score >= threshold

        prog(60, "detectando cenas")
        log("Detectando melhores cenas...")

        segmentos = []
        em_cena   = False
        inicio    = 0.0
        for t, a in zip(times, ativo):
            if a and not em_cena:
                em_cena = True; inicio = max(0, t - 2.0)
            elif not a and em_cena:
                em_cena = False
                fim = min(t + 3.0, duracao)
                if fim - inicio >= 5.0:
                    segmentos.append((inicio, fim))
        if em_cena:
            segmentos.append((inicio, min(times[-1]+3.0, duracao)))

        merged = []
        for seg in segmentos:
            if merged and seg[0] - merged[-1][1] < 8.0:
                merged[-1] = (merged[-1][0], seg[1])
            else:
                merged.append(list(seg))

        expandidas = []
        for ini, fim in merged:
            dur = fim - ini
            if dur < 60.0:
                falta = 60.0 - dur
                ini = max(0, ini - falta/2)
                fim = min(duracao, fim + falta/2)
            if fim - ini > 180.0:
                fim = ini + 180.0
            expandidas.append((ini, fim))

        scored = []
        for ini, fim in expandidas:
            i_ini = max(0, int(ini/0.25))
            i_fim = min(len(score)-1, int(fim/0.25))
            sc    = float(np.mean(score[i_ini:i_fim+1]))
            scored.append((ini, fim, sc))
        scored.sort(key=lambda x: -x[2])

        selecionados = []
        dur_total    = 0.0
        for ini, fim, _ in scored:
            d = fim - ini
            if dur_total + d <= 900:
                selecionados.append((ini, fim))
                dur_total += d
            if len(selecionados) >= 8:
                break
        selecionados.sort(key=lambda x: x[0])

        prog(70, "cortando")
        log(f"Cortando {len(selecionados)} cenas...")

        w, h  = FORMATOS.get(body.formato, FORMATOS["9:16"])
        vf    = (f"scale={w}:{h}:force_original_aspect_ratio=increase,"
                 f"crop={w}:{h},setsar=1")
        pasta = tmp_dir / "cenas"
        pasta.mkdir(exist_ok=True)
        nome  = Path(fname).stem[:30]
        cenas_info = []

        for i, (ini, fim) in enumerate(selecionados, 1):
            arq = pasta / f"{nome}_cena{i:02d}.mp4"
            subprocess.run([
                "ffmpeg", "-y", "-fflags", "+discardcorrupt+genpts",
                "-ss", str(ini), "-i", str(video_path),
                "-t", str(fim-ini), "-vf", vf,
                "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                "-c:a", "aac", "-b:a", "128k",
                "-movflags", "+faststart", str(arq)
            ], capture_output=True)
            if arq.exists() and arq.stat().st_size > 1000:
                cenas_info.append({
                    "arquivo": str(arq), "nome": arq.name,
                    "inicio": f"{int(ini//60)}m{int(ini%60):02d}s",
                    "fim":    f"{int(fim//60)}m{int(fim%60):02d}s",
                    "duracao": f"{int(fim-ini)}s",
                })
            prog(70 + int((i/len(selecionados))*28), "cortando")

        corte_jobs[job_id].update({
            "cenas": cenas_info, "status": "concluido", "progresso": 100
        })
        log(f"{len(cenas_info)} cenas prontas!")
        video_path.unlink(missing_ok=True)
        wav_path.unlink(missing_ok=True)

    except Exception as e:
        corte_jobs[job_id].update({"status": "erro", "erro": str(e)})


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0",
                port=int(os.environ.get("PORT", 8000)), reload=False)
