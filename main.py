from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from supabase import create_client, Client
from datetime import datetime
import os
import base64
import traceback
import uuid
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")

VALID_USERS = {
    "n": os.getenv("CODE_N", "1111"),
    "y": os.getenv("CODE_Y", "2222")
}

app = FastAPI(title="Private Chat API v2")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def verify_credentials(user: str, code: str) -> bool:
    if user not in VALID_USERS:
        return False
    return VALID_USERS[user] == code

def verify_token(request: Request) -> str:
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid auth header")
    token = auth_header.split(" ")[1]
    try:
        decoded = base64.b64decode(token).decode()
        user, code = decoded.split(":")
        if not verify_credentials(user, code):
            raise HTTPException(status_code=401, detail="Invalid credentials")
        return user
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")

@app.get("/health")
async def health():
    return {"status": "ok", "service": "Private Chat API v2", "timestamp": datetime.utcnow().isoformat()}

@app.post("/api/auth/login")
async def login(request: Request):
    body = await request.json()
    user = body.get("user")
    code = body.get("code")
    if not verify_credentials(user, code):
        raise HTTPException(status_code=401, detail="Invalid user or code")
    token_data = f"{user}:{code}"
    token = base64.b64encode(token_data.encode()).decode()
    return {"token": token, "user": user, "valid": True}

@app.get("/api/chat/messages")
async def get_messages(request: Request):
    user = verify_token(request)
    try:
        response = supabase.table("private_chat_messages").select("*").order("created_at", desc=False).execute()
        messages = response.data

        # Generar URLs firmadas para archivos
        for msg in messages:
            if msg.get("media_url") and not msg.get("deleted_at"):
                try:
                    signed = supabase.storage.from_("chat-media").create_signed_url(
                        msg["media_url"], 3600
                    )
                    msg["signed_url"] = signed.get("signedURL") or signed.get("signedUrl")
                except:
                    msg["signed_url"] = None

        return JSONResponse(
            content=messages,
            headers={"Cache-Control": "no-store, no-cache, must-revalidate"}
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@app.post("/api/chat/send")
async def send_message(request: Request):
    user = verify_token(request)
    body = await request.json()
    text = body.get("text", "").strip()
    sender = body.get("sender")
    reply_to_id = body.get("reply_to_id")
    media_url = body.get("media_url")
    media_type = body.get("media_type")
    media_name = body.get("media_name")

    if sender not in ["n", "y"]:
        raise HTTPException(status_code=400, detail="Invalid sender")
    if user != sender:
        raise HTTPException(status_code=403, detail="Cannot send as different user")
    if not text and not media_url:
        raise HTTPException(status_code=400, detail="Message must have text or media")

    try:
        now = datetime.utcnow().isoformat()
        insert_data = {
            "sender": sender,
            "timestamp": now,
            "created_at": now
        }
        if text:
            insert_data["text"] = text
        if reply_to_id:
            insert_data["reply_to_id"] = reply_to_id
        if media_url:
            insert_data["media_url"] = media_url
            insert_data["media_type"] = media_type
            insert_data["media_name"] = media_name

        response = supabase.table("private_chat_messages").insert(insert_data).execute()
        new_msg = response.data[0]

        # Generar URL firmada si tiene media
        if new_msg.get("media_url"):
            try:
                signed = supabase.storage.from_("chat-media").create_signed_url(
                    new_msg["media_url"], 3600
                )
                new_msg["signed_url"] = signed.get("signedURL") or signed.get("signedUrl")
            except:
                new_msg["signed_url"] = None

        return new_msg
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save message: {str(e)}\n{traceback.format_exc()}")

@app.post("/api/chat/upload")
async def upload_file(request: Request):
    user = verify_token(request)
    try:
        form = await request.form()
        file = form.get("file")
        if not file:
            raise HTTPException(status_code=400, detail="No file provided")

        content = await file.read()
        filename = file.filename
        content_type = file.content_type

        # Nombre unico para el archivo
        ext = filename.split(".")[-1] if "." in filename else ""
        unique_name = f"{uuid.uuid4().hex}.{ext}" if ext else uuid.uuid4().hex
        path = f"{user}/{unique_name}"

        # Subir a Supabase Storage
        supabase.storage.from_("chat-media").upload(
            path,
            content,
            {"content-type": content_type}
        )

        return {
            "media_url": path,
            "media_type": content_type,
            "media_name": filename
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")

@app.put("/api/chat/edit/{message_id}")
async def edit_message(message_id: int, request: Request):
    user = verify_token(request)
    body = await request.json()
    new_text = body.get("text", "").strip()

    if not new_text:
        raise HTTPException(status_code=400, detail="New text cannot be empty")

    try:
        # Verificar que el mensaje pertenece al usuario
        msg = supabase.table("private_chat_messages").select("sender").eq("id", message_id).execute()
        if not msg.data:
            raise HTTPException(status_code=404, detail="Message not found")
        if msg.data[0]["sender"] != user:
            raise HTTPException(status_code=403, detail="Cannot edit other user's message")

        now = datetime.utcnow().isoformat()
        result = supabase.table("private_chat_messages").update({
            "text": new_text,
            "edited_at": now
        }).eq("id", message_id).execute()

        return result.data[0]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Edit failed: {str(e)}")

@app.delete("/api/chat/message/{message_id}")
async def delete_message(message_id: int, request: Request):
    user = verify_token(request)
    try:
        # Verificar que el mensaje pertenece al usuario
        msg = supabase.table("private_chat_messages").select("sender, media_url").eq("id", message_id).execute()
        if not msg.data:
            raise HTTPException(status_code=404, detail="Message not found")
        if msg.data[0]["sender"] != user:
            raise HTTPException(status_code=403, detail="Cannot delete other user's message")

        now = datetime.utcnow().isoformat()
        # Soft delete - marcamos como borrado pero no eliminamos
        result = supabase.table("private_chat_messages").update({
            "deleted_at": now,
            "text": None,
            "media_url": None
        }).eq("id", message_id).execute()

        return {"status": "deleted", "id": message_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Delete failed: {str(e)}")

@app.post("/api/chat/read")
async def mark_read(request: Request):
    user = verify_token(request)
    try:
        now = datetime.utcnow().isoformat()
        # Marcar como leídos todos los mensajes del OTRO usuario que aún no tienen read_at
        other = "y" if user == "n" else "n"
        supabase.table("private_chat_messages").update({
            "read_at": now
        }).eq("sender", other).is_("read_at", "null").execute()

        return {"status": "ok", "read_at": now}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Mark read failed: {str(e)}")

@app.delete("/api/chat/clear")
async def clear_chat(request: Request):
    user = verify_token(request)
    if user not in ["n", "y"]:
        raise HTTPException(status_code=403, detail="Unauthorized")
    try:
        supabase.table("private_chat_messages").delete().neq("id", 0).execute()
        return {"status": "cleared", "timestamp": datetime.utcnow().isoformat()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Clear failed: {str(e)}")

@app.get("/")
async def root():
    return {"name": "Private Chat API", "version": "2.0"}
