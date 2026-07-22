from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from supabase import create_client, Client
from datetime import datetime
import os
import base64
import traceback
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")

VALID_USERS = {
    "n": os.getenv("CODE_N", "1111"),
    "y": os.getenv("CODE_Y", "2222")
}

app = FastAPI(title="Private Chat API")

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
    return {"status": "ok", "service": "Private Chat API", "timestamp": datetime.utcnow().isoformat()}

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
        return JSONResponse(
            content=response.data,
            headers={"Cache-Control": "no-store, no-cache, must-revalidate"}
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@app.post("/api/chat/send")
async def send_message(request: Request):
    user = verify_token(request)
    body = await request.json()
    text = body.get("text", "").strip()
    sender = body.get("sender")

    if sender not in ["n", "y"]:
        raise HTTPException(status_code=400, detail="Invalid sender")
    if user != sender:
        raise HTTPException(status_code=403, detail="Cannot send as different user")
    if not text:
        raise HTTPException(status_code=400, detail="Empty message")

    try:
        now = datetime.utcnow().isoformat()
        insert_data = {"text": text, "sender": sender, "timestamp": now, "created_at": now}
        response = supabase.table("private_chat_messages").insert(insert_data).execute()
        if not response.data:
            raise HTTPException(status_code=500, detail="Insert returned no data")
        return response.data[0]
    except HTTPException:
        raise
    except Exception as e:
        error_detail = f"{type(e).__name__}: {str(e)}\n{traceback.format_exc()}"
        raise HTTPException(status_code=500, detail=error_detail)

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
    return {"name": "Private Chat API", "version": "1.0"}
