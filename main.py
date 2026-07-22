from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from supabase import create_client, Client
from datetime import datetime
from typing import List
import os
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
    allow_origins=[FRONTEND_URL, "http://localhost:3000", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

class Message(BaseModel):
    text: str
    sender: str

class MessageResponse(BaseModel):
    id: int
    text: str
    sender: str
    timestamp: str
    created_at: str

class LoginRequest(BaseModel):
    user: str
    code: str

class LoginResponse(BaseModel):
    token: str
    user: str
    valid: bool

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
        import base64
        decoded = base64.b64decode(token).decode()
        user, code = decoded.split(":")
        if not verify_credentials(user, code):
            raise HTTPException(status_code=401, detail="Invalid credentials")
        return user
    except:
        raise HTTPException(status_code=401, detail="Invalid token")

@app.get("/health")
async def health():
    return {"status": "ok", "service": "Private Chat API", "timestamp": datetime.utcnow().isoformat()}

@app.post("/api/auth/login", response_model=LoginResponse)
async def login(req: LoginRequest):
    if not verify_credentials(req.user, req.code):
        raise HTTPException(status_code=401, detail="Invalid user or code")
    import base64
    token_data = f"{req.user}:{req.code}"
    token = base64.b64encode(token_data.encode()).decode()
    return LoginResponse(token=token, user=req.user, valid=True)

@app.get("/api/chat/messages", response_model=List[MessageResponse])
async def get_messages(request: Request):
    user = verify_token(request)
    try:
        response = supabase.table("private_chat_messages").select("*").order("created_at", desc=False).execute()
        messages = response.data
        return [MessageResponse(id=msg["id"], text=msg["text"], sender=msg["sender"], timestamp=msg.get("timestamp", msg["created_at"]), created_at=msg["created_at"]) for msg in messages]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@app.post("/api/chat/send", response_model=MessageResponse)
async def send_message(msg: Message, request: Request):
    user = verify_token(request)
    if msg.sender not in ["n", "y"]:
        raise HTTPException(status_code=400, detail="Invalid sender")
    if user != msg.sender:
        raise HTTPException(status_code=403, detail="Cannot send as different user")
    if not msg.text.strip():
        raise HTTPException(status_code=400, detail="Empty message")
    try:
        now = datetime.utcnow().isoformat()
        insert_data = {"text": msg.text.strip(), "sender": msg.sender, "timestamp": now, "created_at": now}
        response = supabase.table("private_chat_messages").insert(insert_data).execute()
        new_msg = response.data[0]
        return MessageResponse(id=new_msg["id"], text=new_msg["text"], sender=new_msg["sender"], timestamp=new_msg.get("timestamp", new_msg["created_at"]), created_at=new_msg["created_at"])
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save message: {str(e)}")

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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
