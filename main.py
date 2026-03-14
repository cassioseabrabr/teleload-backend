import os
import asyncio
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
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

# Pega do .env (Railway injeta automaticamente)
API_ID   = int(os.environ["TELEGRAM_API_ID"])
API_HASH = os.environ["TELEGRAM_API_HASH"]

# Sessões temporárias em memória (phone -> client ainda não autenticado)
pending_clients: dict[str, TelegramClient] = {}

# Sessões autenticadas (session_string -> client)
active_clients: dict[str, TelegramClient] = {}


# ---------- MODELS ----------

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


# ---------- HELPERS ----------

async def get_client(session: str) -> TelegramClient:
    if session not in active_clients:
        client = TelegramClient(StringSession(session), API_ID, API_HASH)
        await client.connect()
        active_clients[session] = client
    return active_clients[session]


# ---------- ROTAS ----------

@app.get("/")
async def health():
    return {"status": "ok", "service": "TeleLoad API"}


@app.post("/auth/send-code")
async def send_code(body: SendCodeRequest):
    """Passo 1: envia o código SMS/Telegram para o número"""
    client = TelegramClient(StringSession(), API_ID, API_HASH)
    await client.connect()

    result = await client.send_code_request(body.phone)
    pending_clients[body.phone] = client

    return {"phone_code_hash": result.phone_code_hash}


@app.post("/auth/verify-code")
async def verify_code(body: VerifyCodeRequest):
    """Passo 2: verifica o código e retorna a session string"""
    client = pending_clients.get(body.phone)
    if not client:
        raise HTTPException(400, "Sessão não encontrada. Solicite o código novamente.")

    try:
        await client.sign_in(
            phone=body.phone,
            code=body.code,
            phone_code_hash=body.phone_code_hash,
        )
    except PhoneCodeInvalidError:
        raise HTTPException(400, "Código inválido.")
    except SessionPasswordNeededError:
        # Conta tem 2FA — precisamos da senha
        return {"requires_2fa": True}

    session_string = client.session.save()
    active_clients[session_string] = client
    del pending_clients[body.phone]

    me = await client.get_me()
    return {
        "session": session_string,
        "user": {
            "name": f"{me.first_name or ''} {me.last_name or ''}".strip(),
            "username": me.username,
            "phone": me.phone,
        }
    }


@app.post("/auth/verify-2fa")
async def verify_2fa(body: PasswordRequest):
    """Passo 2B: verifica senha do 2FA"""
    client = pending_clients.get(body.phone)
    if not client:
        raise HTTPException(400, "Sessão não encontrada.")

    await client.sign_in(password=body.password)
    session_string = client.session.save()
    active_clients[session_string] = client
    del pending_clients[body.phone]

    me = await client.get_me()
    return {
        "session": session_string,
        "user": {
            "name": f"{me.first_name or ''} {me.last_name or ''}".strip(),
            "username": me.username,
        }
    }


@app.post("/channels")
async def list_channels(body: ChannelsRequest):
    """Lista todos os canais/grupos que o usuário participa"""
    client = await get_client(body.session)
    dialogs = await client.get_dialogs()

    channels = []
    for d in dialogs:
        if d.is_channel or d.is_group:
            channels.append({
                "id": d.id,
                "name": d.name,
                "type": "canal" if d.is_channel else "grupo",
                "unread": d.unread_count,
            })

    return {"channels": channels}


@app.post("/files")
async def list_files(body: FilesRequest):
    """Lista arquivos (documentos) de um canal"""
    client = await get_client(body.session)

    files = []
    async for msg in client.iter_messages(body.channel_id, limit=50):
        if msg.document:
            attrs = {type(a).__name__: a for a in msg.document.attributes}
            name = (
                getattr(attrs.get("DocumentAttributeFilename"), "file_name", None)
                or getattr(attrs.get("DocumentAttributeVideo"), "file_name", None)
                or f"arquivo_{msg.id}"
            )
            files.append({
                "message_id": msg.id,
                "name": name,
                "size": msg.document.size,
                "size_fmt": fmt_size(msg.document.size),
                "mime": msg.document.mime_type,
                "date": msg.date.strftime("%d/%m/%Y"),
            })

    return {"files": files}


@app.post("/download")
async def download_file(body: DownloadRequest):
    """Faz o streaming do arquivo direto para o browser do usuário"""
    client = await get_client(body.session)
    msg = await client.get_messages(body.channel_id, ids=body.message_id)

    if not msg or not msg.document:
        raise HTTPException(404, "Arquivo não encontrado.")

    attrs = {type(a).__name__: a for a in msg.document.attributes}
    filename = getattr(attrs.get("DocumentAttributeFilename"), "file_name", f"arquivo_{msg.id}")

    async def file_stream():
        async for chunk in client.iter_download(msg.document, chunk_size=512 * 1024):
            yield chunk

    return StreamingResponse(
        file_stream(),
        media_type=msg.document.mime_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def fmt_size(size: int) -> str:
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), reload=False)
