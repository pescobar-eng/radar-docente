import os
import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from playwright.async_api import async_playwright


DATA = Path(os.getenv("DATA_FILE", "/data/data.json"))
PROFILE = Path(os.getenv("BROWSER_PROFILE", "/data/browser-profile"))
FRONTEND = Path("/app/frontend")

CABA_URL = "https://actopublico.bue.edu.ar/"
ABC_URL = "https://misservicios.abc.gob.ar/actos.publicos.digitales/"

KEYWORDS = [
    "danza",
    "danza clásica",
    "danza contemporánea",
    "danzas folklóricas",
    "danzas folclóricas",
    "tango",
    "educación artística",
    "artística",
    "preceptor",
    "preceptora",
    "preceptoría",
]

app = FastAPI(title="Radar Docente API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def load():
    if DATA.exists():
        try:
            return json.loads(DATA.read_text(encoding="utf-8"))
        except Exception:
            pass

    return {
        "checked_at": None,
        "items": []
    }


def save(data):
    DATA.parent.mkdir(parents=True, exist_ok=True)
    DATA.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def norm(value):
    return " ".join((value or "").lower().split())


def make_id(source, url, title, raw=""):
    text = f"{source}|{url}|{title}|{raw}"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:20]


def classify(text):
    text = norm(text)

    if "danz" in text or "tango" in text:
        return "Danza"

    if "preceptor" in text:
        return "Preceptoría"

    return "Educación Artística"


async def scrape_caba():
    results = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        page = await browser.new_page()

        await page.goto(
            CABA_URL,
            wait_until="domcontentloaded",
            timeout=60000
        )

        await page.wait_for_timeout(2500)

        seen = set()

        for link in await page.locator("a").all():
            try:
                title = (await link.inner_text()).strip()
                href = await link.get_attribute("href")
            except Exception:
                continue

            if not title or not href:
                continue

            if href.startswith("/"):
                href = "https://actopublico.bue.edu.ar" + href

            text = norm(title)

            if any(keyword in text for keyword in KEYWORDS):
                if href in seen:
                    continue

                seen.add(href)

                results.append({
                    "source": "CABA",
                    "zona": "CABA",
                    "area": classify(text),
                    "nivel": "Todos",
                    "titulo": title,
                    "institucion": "",
                    "cargo": "",
                    "caracter": "",
                    "horas": "",
                    "fecha": "",
                    "estado": "",
                    "url": href,
                    "raw": title,
                })

        await browser.close()

    return results


async def scrape_abc():
    results = []

    async with async_playwright() as p:

        context = await p.chromium.launch_persistent_context(
            str(PROFILE),
            headless=True
        )

        page = (
            context.pages[0]
            if context.pages
            else await context.new_page()
        )

        await page.goto(
            ABC_URL,
            wait_until="domcontentloaded",
            timeout=60000
        )

        await page.wait_for_timeout(3000)

        body = norm(await page.locator("body").inner_text())

        # Nunca se almacenan contraseñas.
        # La consulta utiliza solamente la sesión persistente del navegador.

        if "avellaneda" in body:

            for keyword in KEYWORDS:

                if keyword in body:

                    results.append({
                        "source": "ABC",
                        "zona": "Avellaneda",
                        "area": classify(keyword),
                        "nivel": "Todos",
                        "titulo": (
                            f"Coincidencia APD Avellaneda: "
                            f"{keyword.title()}"
                        ),
                        "institucion": "",
                        "cargo": "",
                        "caracter": "",
                        "horas": "",
                        "fecha": datetime.now().date().isoformat(),
                        "estado": "Revisar publicación en APD",
                        "url": ABC_URL,
                        "raw": keyword,
                    })

        await context.close()

    return results


async def scrape_all():

    results = []

    try:
        results.extend(await scrape_caba())
    except Exception:
        pass

    try:
        results.extend(await scrape_abc())
    except Exception:
        pass

    return results


@app.get("/")
async def home():

    return FileResponse(
        FRONTEND / "index.html"
    )


@app.get("/manifest.webmanifest")
async def manifest():

    return FileResponse(
        FRONTEND / "manifest.webmanifest",
        media_type="application/manifest+json"
    )


@app.get("/sw.js")
async def service_worker():

    return FileResponse(
        FRONTEND / "sw.js",
        media_type="application/javascript"
    )


@app.get("/api/oportunidades")
async def oportunidades():

    return load()


@app.post("/api/actualizar")
async def actualizar():

    database = load()

    old_ids = {
        item.get("id")
        for item in database.get("items", [])
        if item.get("id")
    }

    fresh = await scrape_all()

    items = []

    now = datetime.now(timezone.utc).isoformat()

    for item in fresh:

        item["id"] = make_id(
            item.get("source", ""),
            item.get("url", ""),
            item.get("titulo", ""),
            item.get("raw", "")
        )

        item["nueva"] = item["id"] not in old_ids

        items.append(item)

    data = {
        "checked_at": now,
        "items": items
    }

    save(data)

    return data


@app.get("/api/salud")
async def salud():

    data = load()

    return {
        "ok": True,
        "checked_at": data.get("checked_at"),
        "items": len(data.get("items", []))
    }
