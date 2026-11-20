import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional
from uuid import UUID

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
    url, key = os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key: raise HTTPException(503, "Supabase is not configured")
    return create_client(url, key)

class AuthPayload(BaseModel):
    username: str = Field(min_length=3, max_length=40); password: str = Field(min_length=8, max_length=128); role: Literal["youth", "servant"] = "youth"; name: Optional[str] = Field(default=None, max_length=100); gender: Optional[Literal["boy", "girl"]] = None; grade: Optional[Literal["1", "2", "3"]] = None
class AwardIn(BaseModel):
    youth_id: UUID; category: Literal["early", "bible_read", "verse_recite", "involvement", "misc"]; points: int = Field(gt=0, le=100); note: Optional[str] = Field(default=None, max_length=240)
class YouthIn(BaseModel):
    name: str = Field(min_length=1, max_length=100); gender: Literal["boy", "girl"]; grade: Optional[str] = Field(default=None, max_length=40)

def profile_for(token: Optional[str]):
    if not token: raise HTTPException(401, "Login required")
    client=db()
    try: user=client.auth.get_user(token).user
    except Exception: raise HTTPException(401, "Invalid or expired login")
    profile=client.table("profiles").select("user_id,username,email,role,youth_id").eq("user_id", user.id).maybe_single().execute().data
    if not profile: raise HTTPException(403, "Account profile is not configured")
    return user, profile

def current_profile(authorization: Optional[str] = Header(None)):
    return profile_for(authorization[7:] if authorization and authorization.startswith("Bearer ") else None)

def account_email(username: str) -> str:
    username = username.strip().lower()
    if not re.fullmatch(r"[a-z0-9_]{3,40}", username): raise HTTPException(400, "Username must be 3-40 characters: letters, numbers, or underscores only")
    return f"{username}@accounts.imago-youth.local"

def require_role(role: str):
    def dep(item=Depends(current_profile)):
        if item[1]["role"] != role: raise HTTPException(403, f"{role.title()} access required")
        return item
    return dep

@app.get("/api/health")
def health(): return {"ok": True, "service": "imago-youth"}

@app.post("/api/auth/signup")
def signup(payload: AuthPayload):
    client=db()
    username=payload.username.strip().lower(); internal_email=account_email(username)
    if payload.role == "youth" and (not payload.name or not payload.gender): raise HTTPException(400, "Name and gender are required for youth accounts")
    try: result=client.auth.admin.create_user({"email": internal_email, "password": payload.password, "email_confirm": True, "user_metadata": {"username": username, "role": payload.role}})
    except Exception as exc: raise HTTPException(400, str(exc))
    user=result.user
    if not user: raise HTTPException(400, "Could not create account")
    youth_id=None
    if payload.role == "youth": youth_id=client.table("youth").insert({"name":payload.name,"gender":payload.gender,"grade":payload.grade}).execute().data[0]["id"]
    client.table("profiles").upsert({"user_id":user.id,"username":username,"email":internal_email,"role":payload.role,"youth_id":youth_id}).execute()
    try: session=client.auth.sign_in_with_password({"email":internal_email,"password":payload.password}).session
    except Exception: session=None
    return {"access_token": session.access_token if session else None, "message": "Account created. You can now log in." if not session else "Account created."}

@app.post("/api/auth/login")
def login(payload: AuthPayload):
    try: result=db().auth.sign_in_with_password({"email":account_email(payload.username),"password":payload.password})
    except Exception: raise HTTPException(401, "Incorrect email or password")
    if not result.session: raise HTTPException(401, "Please confirm your email before logging in")
    profile=db().table("profiles").select("role,youth_id,username").eq("user_id", result.user.id).maybe_single().execute().data
    if not profile: raise HTTPException(403, "Account profile is not configured")
    return {"access_token":result.session.access_token,"user":{"username":profile["username"],"role":profile["role"],"youth_id":profile["youth_id"]}}

@app.get("/api/auth/me")
def me(item=Depends(current_profile)):
    user, profile=item; return {"username":profile["username"],"role":profile["role"],"youth_id":profile["youth_id"]}

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
    _, profile=item
    if profile["role"]=="youth" and str(profile.get("youth_id"))!=str(youth_id): raise HTTPException(403,"You can only view your own notifications")
    return db().table("notifications").select("id,points,category,note,awarded_by,created_at,read_at").eq("youth_id",str(youth_id)).order("created_at",desc=True).limit(100).execute().data

@app.post("/api/notifications/{youth_id}/read")
def mark_read(youth_id: UUID, item=Depends(current_profile)):
    _, profile=item
    if profile["role"]=="youth" and str(profile.get("youth_id"))!=str(youth_id): raise HTTPException(403,"You can only update your own notifications")
    db().table("notifications").update({"read_at":datetime.now(timezone.utc).isoformat()}).eq("youth_id",str(youth_id)).is_("read_at","null").execute(); return {"ok":True}

@app.post("/api/awards")
def award(payload: AwardIn, item=Depends(require_role("servant"))):
    user, _=item; client=db(); youth=client.table("youth").select("id").eq("id",str(payload.youth_id)).eq("active",True).maybe_single().execute().data
    if not youth: raise HTTPException(404,"Youth profile not found")
    row={"youth_id":str(payload.youth_id),"category":payload.category,"points":payload.points,"note":payload.note,"awarded_by":user.email}
    client.table("awards").insert(row).execute(); client.table("notifications").insert(row).execute(); return {"ok":True}

@app.post("/api/youth")
def add_youth(payload: YouthIn, _=Depends(require_role("servant"))): return db().table("youth").insert(payload.model_dump()).execute().data[0]

@app.delete("/api/youth/{youth_id}")
def remove_youth(youth_id: UUID, _=Depends(require_role("servant"))): db().table("youth").update({"active":False}).eq("id",str(youth_id)).execute(); return {"ok":True}

@app.get("/{path:path}")
def frontend(path: str): return FileResponse(BASE/"index.html")
