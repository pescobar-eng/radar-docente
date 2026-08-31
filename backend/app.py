
import os, json, hashlib, asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from playwright.async_api import async_playwright

DATA=Path(os.getenv("DATA_FILE","data.json"))
PROFILE=Path(os.getenv("BROWSER_PROFILE","./browser-profile"))
CABA_URL="https://actopublico.bue.edu.ar/"
ABC_URL="https://misservicios.abc.gob.ar/actos.publicos.digitales/"

KEYWORDS=[
 "danza","danza clásica","danza contemporánea","danzas folklóricas","danzas folclóricas",
 "tango","educación artística","artística","preceptor","preceptora","preceptoría"
]
app=FastAPI(title="Radar Docente API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

def load():
    if DATA.exists():
        try:return json.loads(DATA.read_text(encoding="utf-8"))
        except: pass
    return {"checked_at":None,"items":[]}

def save(d): DATA.write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding="utf-8")

def norm(s): return " ".join((s or "").lower().split())

def uid(source,url,title,raw=""):
    return hashlib.sha256((source+"|"+url+"|"+title+"|"+raw).encode()).hexdigest()[:20]

async def scrape_caba():
    out=[]
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True)
        page=await browser.new_page()
        await page.goto(CABA_URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(3000)
        links=await page.locator("a").all()
        seen=set()
        for a in links:
            txt=(await a.inner_text()).strip()
            href=await a.get_attribute("href")
            if not href or not txt: continue
            if href.startswith("/"): href="https://actopublico.bue.edu.ar"+href
            t=norm(txt)
            if any(k in t for k in KEYWORDS) and href not in seen:
                seen.add(href)
                out.append({"source":"CABA","zona":"CABA","area":"Danza" if "danz" in t or "tango" in t else ("Preceptoría" if "preceptor" in t else "Educación Artística"),
                "nivel":"Todos","titulo":txt,"institucion":"","cargo":"","caracter":"","horas":"","fecha":"",
                "estado":"","url":href,"raw":txt})
        await browser.close()
    return out

async def scrape_abc():
    out=[]
    async with async_playwright() as p:
        context=await p.chromium.launch_persistent_context(str(PROFILE), headless=True)
        page=context.pages[0] if context.pages else await context.new_page()
        await page.goto(ABC_URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(3000)
        body=norm(await page.locator("body").inner_text())
        # The portal is authenticated. We intentionally do not attempt to capture credentials.
        for k in KEYWORDS:
            if k in body and "avellaneda" in body:
                out.append({"source":"ABC","zona":"Avellaneda","area":"Danza" if "danz" in k or "tango" in k else ("Preceptoría" if "preceptor" in k else "Educación Artística"),
                "nivel":"Todos","titulo":k.title()+" — revisar APD Avellaneda","institucion":"","cargo":"","caracter":"","horas":"","fecha":datetime.now().date().isoformat(),
                "estado":"Detectada en sesión ABC","url":ABC_URL,"raw":k})
        await context.close()
    return out

async def scrape_all():
    results=[]
    try: results += await scrape_caba()
    except Exception as e: results.append({"source":"CABA","error":str(e)})
    try: results += await scrape_abc()
    except Exception as e: results.append({"source":"ABC","error":str(e)})
    return results

@app.get("/api/oportunidades")
async def oportunidades():
    return load()

@app.post("/api/actualizar")
async def actualizar():
    db=load()
    old={x.get("id") for x in db.get("items",[])}
    fresh=await scrape_all()
    items=[]
    now=datetime.now(timezone.utc).isoformat()
    for x in fresh:
        if "error" in x: continue
        x["id"]=uid(x.get("source",""),x.get("url",""),x.get("titulo",""),x.get("raw",""))
        x["nueva"]=x["id"] not in old
        items.append(x)
    # newest first; preserve only current snapshot
    db={"checked_at":now,"items":items}
    save(db)
    return db

@app.get("/api/salud")
async def salud():
    d=load()
    return {"ok":True,"checked_at":d.get("checked_at"),"items":len(d.get("items",[]))}
