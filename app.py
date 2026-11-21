import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional
from uuid import UUID, uuid4

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from supabase import Client, create_client

load_dotenv()
BASE = Path(__file__).resolve().parent
app = FastAPI(title="Imago Youth API", version="2.0.0")
app.add_middleware(CORSMiddleware, allow_origins=os.getenv("CORS_ORIGINS", "*").split(","), allow_methods=["*"], allow_headers=["*"])

def db() -> Client:
    url = (os.getenv("SUPABASE_URL") or "").strip().strip('"').strip("'")
    key = (os.getenv("SUPABASE_SERVICE_ROLE_KEY") or "").strip().strip('"').strip("'")
    if not url or not key: raise HTTPException(503, "Supabase is not configured")
    if key.startswith("eyJ"):
        try:
            role=json.loads(base64.urlsafe_b64decode(key.split(".")[1]+"===").decode()).get("role")
            if role != "service_role": raise HTTPException(503, "SUPABASE_SERVICE_ROLE_KEY is an anon key. Copy the service_role key, not the anon key.")
        except HTTPException: raise
        except Exception: raise HTTPException(503, "SUPABASE_SERVICE_ROLE_KEY is not a valid Supabase JWT")
    try:
        return create_client(url, key)
    except Exception as exc:
        raise HTTPException(503, "Invalid SUPABASE_SERVICE_ROLE_KEY. Copy a valid server-side Supabase secret/service-role key from the same project.") from exc

def first_row(query):
    rows = query.execute().data or []
    return rows[0] if rows else None

class AuthPayload(BaseModel):
    username: str = Field(min_length=3, max_length=40); password: str = Field(min_length=8, max_length=128); role: Literal["youth", "servant"] = "youth"; name: Optional[str] = Field(default=None, max_length=100); gender: Optional[Literal["boy", "girl"]] = None; grade: Optional[Literal["1", "2", "3"]] = None
class AwardIn(BaseModel):
    youth_id: UUID; category: Literal["early", "bible_read", "verse_recite", "involvement", "misc"]; points: int = Field(ge=-100, le=100); note: Optional[str] = Field(default=None, max_length=240)
class YouthIn(BaseModel):
    name: str = Field(min_length=1, max_length=100); gender: Literal["boy", "girl"]; grade: Optional[str] = Field(default=None, max_length=40)

def password_hash(password: str, salt: Optional[str] = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120_000).hex()
    return f"pbkdf2_sha256$120000${salt}${digest}"

def password_ok(password: str, stored: str) -> bool:
    try: _, rounds, salt, digest = stored.split("$", 3)
    except ValueError: return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(rounds)).hex()
    return hmac.compare_digest(candidate, digest)

def session_token(user_id: str) -> str:
    expiry = int(time.time()) + 86400 * 7
    body = f"{user_id}:{expiry}"
    secret = os.getenv("APP_SECRET", "change-this-secret")
    signature = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}:{signature}"

def account_for(token: Optional[str]):
    if not token: raise HTTPException(401, "Login required")
    try: user_id, expiry, signature = token.split(":", 2); body=f"{user_id}:{expiry}"
    except ValueError: raise HTTPException(401, "Invalid or expired login")
    expected = hmac.new(os.getenv("APP_SECRET", "change-this-secret").encode(), body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected) or int(expiry) < int(time.time()): raise HTTPException(401, "Invalid or expired login")
    account=first_row(db().table("accounts").select("user_id,username,password_hash,role,youth_id").eq("user_id", user_id))
    if not account: raise HTTPException(401, "Invalid or expired login")
    return account

def current_profile(authorization: Optional[str] = Header(None)):
    return account_for(authorization[7:] if authorization and authorization.startswith("Bearer ") else None)

def account_email(username: str) -> str:
    username = username.strip().lower()
    if not re.fullmatch(r"[a-z0-9_]{3,40}", username): raise HTTPException(400, "Username must be 3-40 characters: letters, numbers, or underscores only")
    return f"{username}@accounts.imago-youth.local"

def require_role(role: str):
    def dep(item=Depends(current_profile)):
        if item["role"] != role: raise HTTPException(403, f"{role.title()} access required")
        return item
    return dep

@app.get("/api/health")
def health():
    key=(os.getenv("SUPABASE_SERVICE_ROLE_KEY") or "").strip().strip('"').strip("'")
    legacy_role=None
    if key.startswith("eyJ"):
        try: legacy_role=json.loads(base64.urlsafe_b64decode(key.split(".")[1]+"===").decode()).get("role")
        except Exception: legacy_role="invalid-jwt"
    return {"ok":True,"service":"imago-youth","supabase_url_set":bool(os.getenv("SUPABASE_URL")),"key_prefix":key[:12],"key_length":len(key),"legacy_role":legacy_role}

@app.post("/api/auth/signup")
def signup(payload: AuthPayload):
    client=db()
    username=payload.username.strip().lower()
    if not re.fullmatch(r"[a-z0-9_]{3,40}", username): raise HTTPException(400, "Username must be 3-40 characters: letters, numbers, or underscores only")
    if payload.role == "youth" and (not payload.name or not payload.gender): raise HTTPException(400, "Name and gender are required for youth accounts")
    try:
        existing = first_row(client.table("accounts").select("user_id").eq("username", username))
    except Exception as exc:
        if "permission denied" in str(exc).lower() or "42501" in str(exc):
            raise HTTPException(503, "Supabase is using an anon/publishable key. Set SUPABASE_SERVICE_ROLE_KEY in Render to the server-side service_role/secret key from this same project.") from exc
        raise
    if existing: raise HTTPException(409, "Username is already taken")
    youth_id=None
    if payload.role == "youth": youth_id=client.table("youth").insert({"name":payload.name,"gender":payload.gender,"grade":payload.grade}).execute().data[0]["id"]
    user_id=str(uuid4())
    client.table("accounts").insert({"user_id":user_id,"username":username,"password_hash":password_hash(payload.password),"role":payload.role,"youth_id":youth_id}).execute()
    return {"access_token":session_token(user_id), "message":"Account created."}

@app.post("/api/auth/login")
def login(payload: AuthPayload):
    username=payload.username.strip().lower(); account=first_row(db().table("accounts").select("user_id,username,password_hash,role,youth_id").eq("username", username))
    if not account or not password_ok(payload.password, account["password_hash"]): raise HTTPException(401, "Incorrect username or password")
    return {"access_token":session_token(account["user_id"]),"user":{"username":account["username"],"role":account["role"],"youth_id":account["youth_id"]}}

@app.get("/api/auth/me")
def me(item=Depends(current_profile)):
    return {"username":item["username"],"role":item["role"],"youth_id":item["youth_id"]}

@app.get("/api/leaderboard")
def leaderboard():
    client=db(); rows=client.table("youth").select("id,name,gender,grade").eq("active",True).order("name").execute().data; awards=client.table("awards").select("youth_id,category,points").execute().data
    totals={str(r["id"]):{"total":0,"early":0,"bible_read":0,"verse_recite":0,"involvement":0,"misc":0} for r in rows}
    for a in awards:
        if str(a["youth_id"]) in totals: totals[str(a["youth_id"])] ["total"]+=a["points"]; totals[str(a["youth_id"])] [a["category"]]+=a["points"]
    out=[]
    for r in rows:
        p=totals[str(r["id"])]; out.append({"id":str(r["id"]),"name":r["name"],"gender":r["gender"],"grade":r.get("grade"),"total":p["total"],"points":{k:v for k,v in p.items() if k!="total"}})
    out.sort(key=lambda x:x["total"],reverse=True); last=None; rank=0
    for i,r in enumerate(out):
        if r["total"]!=last: rank=i+1; last=r["total"]
        r["rank"]=rank
    return out

@app.get("/api/notifications/{youth_id}")
def notifications(youth_id: UUID, item=Depends(current_profile)):
    if item["role"]=="youth" and str(item.get("youth_id"))!=str(youth_id): raise HTTPException(403,"You can only view your own notifications")
    return db().table("notifications").select("id,points,category,note,awarded_by,created_at,read_at").eq("youth_id",str(youth_id)).order("created_at",desc=True).limit(100).execute().data

@app.post("/api/notifications/{youth_id}/read")
def mark_read(youth_id: UUID, item=Depends(current_profile)):
    if item["role"]=="youth" and str(item.get("youth_id"))!=str(youth_id): raise HTTPException(403,"You can only update your own notifications")
    db().table("notifications").update({"read_at":datetime.now(timezone.utc).isoformat()}).eq("youth_id",str(youth_id)).is_("read_at","null").execute(); return {"ok":True}

@app.post("/api/awards")
def award(payload: AwardIn, item=Depends(require_role("servant"))):
    if payload.points == 0: raise HTTPException(400, "Points cannot be zero")
    client=db(); youth=first_row(client.table("youth").select("id").eq("id",str(payload.youth_id)).eq("active",True))
    if not youth: raise HTTPException(404,"Youth profile not found")
    row={"youth_id":str(payload.youth_id),"category":payload.category,"points":payload.points,"note":payload.note,"awarded_by":item["username"]}
    client.table("awards").insert(row).execute(); client.table("notifications").insert(row).execute(); return {"ok":True}

@app.post("/api/youth")
def add_youth(payload: YouthIn, _=Depends(require_role("servant"))): return db().table("youth").insert(payload.model_dump()).execute().data[0]

@app.delete("/api/youth/{youth_id}")
def remove_youth(youth_id: UUID, _=Depends(require_role("servant"))): db().table("youth").update({"active":False}).eq("id",str(youth_id)).execute(); return {"ok":True}

@app.get("/{path:path}")
def frontend(path: str): return FileResponse(BASE/"index.html")
