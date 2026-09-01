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

    if "preceptor" in text:
        return "Preceptoría"

    if any(word in text for word in [
        "danza",
        "danzas",
        "tango",
        "expresión corporal",
        "expresion corporal"
    ]):
        return "Danza"

    return None


async def scrape_caba():
    results = []

    # Consultamos directamente el listado público de ARTÍSTICA.
    # También recorremos algunas páginas para no quedarnos solo con la primera.
    urls = [
        "https://actopublico.bue.edu.ar/?areas%5B0%5D=8&asignaturas%5B0%5D=0&cargos%5B0%5D=0&escuelas%5B0%5D=0&especialidades%5B0%5D=0&page=1&status_carousel=1&status_map=1",
    ]

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        for page_number in range(1, 11):

            url = (
                "https://actopublico.bue.edu.ar/"
                "?areas%5B0%5D=8"
                "&asignaturas%5B0%5D=0"
                "&cargos%5B0%5D=0"
                "&escuelas%5B0%5D=0"
                "&especialidades%5B0%5D=0"
                f"&page={page_number}"
                "&status_carousel=1"
                "&status_map=1"
            )

            page = await browser.new_page()

            try:
                await page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=60000
                )

                await page.wait_for_timeout(2500)

                body = await page.locator("body").inner_text()

                # Los cargos aparecen en bloques con "Actos Públicos de la Ciudad".
                # Tomamos los enlaces reales hacia las solicitudes.
                anchors = await page.locator(
                    'a[href*="/solicitud/"]'
                ).all()

                if not anchors:
                    await page.close()
                    break

                seen = set()

                for link in anchors:

                    try:
                        href = await link.get_attribute("href")
                        label = (await link.inner_text()).strip()
                    except Exception:
                        continue

                    if not href:
                        continue

                    if href.startswith("/"):
                        href = (
                            "https://actopublico.bue.edu.ar"
                            + href
                        )

                    if href in seen:
                        continue

                    seen.add(href)

                    # Abrimos el detalle de la solicitud.
                    detail = await browser.new_page()

                    try:
                        await detail.goto(
                            href,
                            wait_until="domcontentloaded",
                            timeout=30000
                        )

                        await detail.wait_for_timeout(700)

                        detail_text = await detail.locator(
                            "body"
                        ).inner_text()

                    except Exception:
                        await detail.close()
                        continue

                    await detail.close()

                    text = detail_text.lower()

                    # Solo nuestras áreas.
                    if not any(
                        palabra in text
                        for palabra in [
                            "danza",
                            "danzas",
                            "tango",
                            "educación artística",
                            "artistica",
                            "artística",
                            "preceptor",
                            "preceptora",
                        ]
                    ):
                        continue

                    # No mostrar cargos que ya fueron asignados.
                    if "asignada" in text:
                        continue

                    # No mostrar cargos cerrados.
                    if "cerrada" in text:
                        continue

                    lines = [
                        line.strip()
                        for line in detail_text.splitlines()
                        if line.strip()
                    ]

                    def buscar(etiquetas):
                        for i, line in enumerate(lines):

                            normal = line.lower()

                            if any(
                                etiqueta in normal
                                for etiqueta in etiquetas
                            ):
                                if i + 1 < len(lines):
                                    return lines[i + 1]

                        return ""

                    # Determinar el área.
                    if (
                        "preceptor" in text
                        or "preceptora" in text
                    ):
                        area = "Preceptoría"

                    elif (
                        "danza" in text
                        or "tango" in text
                    ):
                        area = "Danza"

                    else:
                        area = "Educación Artística"

                    results.append({
                        "source": "CABA",
                        "zona": "CABA",
                        "area": area,

                        "nivel": buscar([
                            "nivel"
                        ]),

                        "titulo": label
                            or "Oportunidad docente CABA",

                        "institucion": buscar([
                            "establecimiento",
                            "escuela"
                        ]),

                        "cargo": buscar([
                            "nombre del cargo",
                            "cargo a cubrir",
                            "cargo",
                            "asignatura",
                            "especialidad"
                        ]),

                        "caracter": buscar([
                            "carácter",
                            "caracter"
                        ]),

                        "horas": buscar([
                            "horas cátedra",
                            "horas",
                            "módulos"
                        ]),

                        "fecha": buscar([
                            "fecha de acto público",
                            "fecha de acto",
                            "acto público"
                        ]),

                        "estado": (
                            "Publicada"
                            if "publicada" in text
                            else ""
                        ),

                        "url": href,

                        "raw": detail_text[:7000]
                    })

                await page.close()

            except Exception:
                await page.close()
                continue

        await browser.close()

    # Eliminar duplicados.
    unique = {}

    for item in results:
        key = (
            item.get("url")
            or item.get("titulo")
        )

        unique[key] = item

    return list(unique.values())

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
