import hashlib, hmac, json, os, time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from supabase import Client, create_client
from dotenv import load_dotenv

load_dotenv()

BASE = Path(__file__).resolve().parent
app = FastAPI(title="Imago Youth API", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=os.getenv("CORS_ORIGINS", "*").split(","), allow_credentials=False, allow_methods=["*"], allow_headers=["*"])

def db() -> Client:
    url, key = os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key: raise HTTPException(503, "Supabase is not configured")
    return create_client(url, key)

def passcodes() -> dict:
    raw = os.getenv("SERVANT_PASSCODES", '{"Marly":"1234","Mondeen":"1234","Ossama":"1234","Nancy":"1234","Michael":"1234"}')
    try: return json.loads(raw)
    except json.JSONDecodeError: raise RuntimeError("SERVANT_PASSCODES must be valid JSON")

def token_for(servant: str) -> str:
    body = f"{servant}:{int(time.time()) + 86400}"
    sig = hmac.new(os.getenv("APP_SECRET", "change-me-in-production").encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}:{sig}"

def current_servant(authorization: Optional[str] = Header(None)) -> str:
    if not authorization or not authorization.startswith("Bearer "): raise HTTPException(401, "Servant login required")
    try: servant, expiry, sig = authorization[7:].split(":", 2); body=f"{servant}:{expiry}"
    except ValueError: raise HTTPException(401, "Invalid session")
    expected = hmac.new(os.getenv("APP_SECRET", "change-me-in-production").encode(), body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected) or int(expiry) < int(time.time()) or servant not in passcodes(): raise HTTPException(401, "Expired session")
    return servant

class Login(BaseModel): servant: str; passcode: str
class YouthIn(BaseModel): name: str = Field(min_length=1, max_length=100); gender: str; grade: Optional[str] = Field(default=None, max_length=40)
class AwardIn(BaseModel): youth_id: UUID; category: str; points: int = Field(gt=0, le=100); note: Optional[str] = Field(default=None, max_length=240)

@app.get("/api/health")
def health(): return {"ok": True, "service": "imago-youth"}

@app.post("/api/auth/login")
def login(payload: Login):
    codes = passcodes()
    if payload.servant not in codes or not hmac.compare_digest(str(codes[payload.servant]), payload.passcode): raise HTTPException(401, "Incorrect passcode")
    return {"token": token_for(payload.servant), "servant": payload.servant}

@app.get("/api/leaderboard")
def leaderboard():
    client=db(); rows = client.table("youth").select("id,name,gender,grade,active").eq("active", True).order("name").execute().data
    awards = client.table("awards").select("youth_id,category,points").execute().data
    totals = {str(r["id"]): {"total": 0, "early": 0, "bible_read": 0, "verse_recite": 0, "involvement": 0, "misc": 0} for r in rows}
    for a in awards:
        y = totals.get(str(a["youth_id"]))
        if y: y["total"] += a["points"]; y[a["category"]] = y.get(a["category"], 0) + a["points"]
    out=[]
    for r in rows:
        p=totals[str(r["id"])]
        out.append({"id":str(r["id"]),"name":r["name"],"gender":r["gender"],"grade":r.get("grade"),"total":p.pop("total"),"points":p})
    out.sort(key=lambda x:x["total"], reverse=True); last=None; rank=0
    for i, r in enumerate(out):
        if r["total"] != last: rank=i+1; last=r["total"]
        r["rank"] = rank
    return out

@app.get("/api/notifications/{youth_id}")
def notifications(youth_id: UUID):
    return db().table("notifications").select("id,points,category,note,awarded_by,created_at,read_at").eq("youth_id", str(youth_id)).order("created_at", desc=True).limit(100).execute().data

@app.post("/api/notifications/{youth_id}/read")
def mark_read(youth_id: UUID):
    db().table("notifications").update({"read_at": datetime.now(timezone.utc).isoformat()}).eq("youth_id", str(youth_id)).is_("read_at", "null").execute(); return {"ok": True}

@app.post("/api/awards")
def award(payload: AwardIn, servant: str = Depends(current_servant)):
    client=db(); youth=client.table("youth").select("id").eq("id", str(payload.youth_id)).eq("active", True).maybe_single().execute().data
    if not youth: raise HTTPException(404, "Youth profile not found")
    award_row={"youth_id":str(payload.youth_id),"category":payload.category,"points":payload.points,"note":payload.note,"awarded_by":servant}
    client.table("awards").insert(award_row).execute(); client.table("notifications").insert(award_row).execute(); return {"ok": True}

@app.post("/api/youth")
def add_youth(payload: YouthIn, _: str = Depends(current_servant)):
    if payload.gender not in {"boy","girl"}: raise HTTPException(400, "Gender must be boy or girl")
    return db().table("youth").insert(payload.model_dump()).execute().data[0]

@app.delete("/api/youth/{youth_id}")
def remove_youth(youth_id: UUID, _: str = Depends(current_servant)):
    db().table("youth").update({"active":False}).eq("id", str(youth_id)).execute(); return {"ok": True}

@app.get("/{path:path}")
def frontend(path: str): return FileResponse(BASE / "index.html")
