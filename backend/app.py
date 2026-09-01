import os
import json
import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from playwright.async_api import async_playwright


# ============================================================
# CONFIGURACIÓN
# ============================================================

DATA = Path(os.getenv("DATA_FILE", "/data/data.json"))
FRONTEND = Path("/app/frontend")

CABA_BASE = "https://actopublico.bue.edu.ar/"
CABA_LIST = "https://actopublico.bue.edu.ar/?todas=true"

SAD_AVELLANEDA = "https://www.sadavellaneda.com.ar/"


# ============================================================
# PALABRAS QUE SÍ NOS INTERESAN
# ============================================================

DANZA_WORDS = [
    "danza",
    "danzas",
    "danza clásica",
    "danza contemporánea",
    "danzas folklóricas",
    "danzas folclóricas",
    "tango",
    "expresión corporal",
    "expresion corporal",
]

PRECEPTORIA_WORDS = [
    "preceptor",
    "preceptora",
    "preceptoría",
    "preceptoria",
    "jefe de preceptores",
    "jefa de preceptores",
]


# ============================================================
# APP
# ============================================================

app = FastAPI(title="Radar Docente API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# UTILIDADES
# ============================================================

def normalize(text):
    return " ".join((text or "").lower().split())


def clean(text):
    return re.sub(r"\s+", " ", text or "").strip()


def load_data():
    if DATA.exists():
        try:
            return json.loads(
                DATA.read_text(encoding="utf-8")
            )
        except Exception:
            pass

    return {
        "checked_at": None,
        "items": [],
        "source_status": {},
    }


def save_data(data):
    DATA.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    DATA.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )


def make_id(source, url, title, raw):
    value = (
        f"{source}|"
        f"{url}|"
        f"{title}|"
        f"{raw}"
    )

    return hashlib.sha256(
        value.encode("utf-8")
    ).hexdigest()[:24]


def classify(text):
    """
    Devuelve únicamente:

    - Danza
    - Preceptoría
    - None

    IMPORTANTE:
    No usamos "Educación Artística" como criterio,
    porque eso incluiría Biología, Educación Física,
    Música, Artes Visuales, etc.
    """

    text = normalize(text)

    for word in PRECEPTORIA_WORDS:
        if word in text:
            return "Preceptoría"

    for word in DANZA_WORDS:
        if word in text:
            return "Danza"

    return None


def get_value(lines, labels):
    """
    Intenta obtener el valor de una etiqueta.

    Soporta formatos como:

    Asignatura
    DANZA CLÁSICA

    o:

    Asignatura: DANZA CLÁSICA
    """

    normalized_labels = [
        normalize(label)
        for label in labels
    ]

    for i, line in enumerate(lines):

        current = normalize(line)

        # Formato:
        # Asignatura: DANZA CLÁSICA
        for label in normalized_labels:

            if current.startswith(label + ":"):

                value = line.split(":", 1)[1].strip()

                if value:
                    return value

        # Formato:
        # Asignatura
        # DANZA CLÁSICA
        if current in normalized_labels:

            if i + 1 < len(lines):
                return lines[i + 1].strip()

    return ""


# ============================================================
# SCRAPER CABA
# ============================================================

async def scrape_caba():

    results = []

    async with async_playwright() as p:

        browser = await p.chromium.launch(
            headless=True
        )

        # ----------------------------------------------------
        # Recorremos páginas del listado público
        # ----------------------------------------------------

        seen_urls = set()

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

                # El listado se completa con JavaScript.
                await page.wait_for_timeout(5000)

                # Scroll para activar posibles contenidos lazy.
                for _ in range(8):

                    await page.mouse.wheel(
                        0,
                        1600
                    )

                    await page.wait_for_timeout(400)

                anchors = await page.locator(
                    'a[href*="/solicitud/"]'
                ).evaluate_all(
                    """
                    elements => elements.map(
                        element => ({
                            href: element.href,
                            text: (
                                element.innerText || ""
                            ).trim()
                        })
                    )
                    """
                )

            except Exception:

                await page.close()
                continue

            await page.close()

            # Si la página no tiene solicitudes,
            # no seguimos avanzando.
            if not anchors:
                break

            # ------------------------------------------------
            # Abrimos cada solicitud
            # ------------------------------------------------

            for anchor in anchors:

                href = anchor.get(
                    "href",
                    ""
                )

                label = clean(
                    anchor.get(
                        "text",
                        ""
                    )
                )

                if not href:
                    continue

                if href in seen_urls:
                    continue

                seen_urls.add(href)

                detail = await browser.new_page()

                try:

                    await detail.goto(
                        href,
                        wait_until="domcontentloaded",
                        timeout=30000
                    )

                    await detail.wait_for_timeout(
                        1000
                    )

                    detail_text = await detail.locator(
                        "body"
                    ).inner_text()

                except Exception:

                    await detail.close()
                    continue

                await detail.close()

                # ------------------------------------------------
                # Convertimos la ficha en líneas
                # ------------------------------------------------

                lines = [
                    clean(line)
                    for line in detail_text.splitlines()
                    if clean(line)
                ]

                # ------------------------------------------------
                # Extraemos campos
                # ------------------------------------------------

                nombre_cargo = get_value(
                    lines,
                    [
                        "nombre del cargo",
                    ]
                )

                asignatura = get_value(
                    lines,
                    [
                        "asignatura",
                    ]
                )

                establecimiento = get_value(
                    lines,
                    [
                        "establecimiento del cargo",
                        "establecimiento",
                    ]
                )

                caracter = get_value(
                    lines,
                    [
                        "carácter",
                        "caracter",
                    ]
                )

                horas = get_value(
                    lines,
                    [
                        "horas cátedra",
                        "horas",
                        "módulos",
                    ]
                )

                nivel = get_value(
                    lines,
                    [
                        "nivel",
                    ]
                )

                fecha = get_value(
                    lines,
                    [
                        "fecha de acto público",
                        "fecha de acto",
                        "acto público",
                    ]
                )

                # ------------------------------------------------
                # FILTRO PRINCIPAL
                #
                # SOLO usamos:
                # nombre del cargo + asignatura
                #
                # No usamos toda la ficha.
                # ------------------------------------------------

                datos_relevantes = (
                    f"{nombre_cargo} "
                    f"{asignatura}"
                )

                area = classify(
                    datos_relevantes
                )

                # No es Danza ni Preceptoría.
                if area is None:
                    continue

                # ------------------------------------------------
                # Estado
                # ------------------------------------------------

                estado_texto = normalize(
                    detail_text
                )

                # Fuera de circulación.
                if "asignada" in estado_texto:
                    continue

                if "cerrada" in estado_texto:
                    continue

                # ------------------------------------------------
                # Resultado
                # ------------------------------------------------

                results.append({

                    "source": "CABA",

                    "zona": "CABA",

                    "area": area,

                    "nivel": nivel,

                    "titulo": (
                        nombre_cargo
                        or label
                        or "Oportunidad docente"
                    ),

                    "institucion": establecimiento,

                    "cargo": (
                        asignatura
                        or nombre_cargo
                    ),

                    "caracter": caracter,

                    "horas": horas,

                    "fecha": fecha,

                    "estado": (
                        "Publicada"
                        if "publicada" in estado_texto
                        else ""
                    ),

                    "url": href,

                    "raw": detail_text[:7000],
                })

        await browser.close()

    # --------------------------------------------------------
    # Eliminar duplicados
    # --------------------------------------------------------

    unique = {}

    for item in results:

        unique[
            item["url"]
        ] = item

    return list(
        unique.values()
    )


# ============================================================
# AVELLANEDA
# ============================================================

async def scrape_avellaneda_public():

    results = []

    async with async_playwright() as p:

        browser = await p.chromium.launch(
            headless=True
        )

        page = await browser.new_page()

        try:

            await page.goto(
                SAD_AVELLANEDA,
                wait_until="domcontentloaded",
                timeout=60000
            )

            await page.wait_for_timeout(
                3000
            )

            links = await page.locator(
                "a"
            ).all()

            for link in links:

                try:

                    label = clean(
                        await link.inner_text()
                    )

                    href = await link.get_attribute(
                        "href"
                    )

                except Exception:

                    continue

                if not label or not href:
                    continue

                # ------------------------------------------------
                # Solamente publicaciones vinculadas con
                # Danza o Preceptoría.
                # ------------------------------------------------

                area = classify(label)

                if area is None:
                    continue

                text = normalize(label)

                if not any(
                    term in text
                    for term in [
                        "acto público",
                        "apd",
                        "cobertura",
                        "convocatoria",
                        "danza",
                        "preceptor",
                    ]
                ):
                    continue

                results.append({

                    "source": "SAD Avellaneda",

                    "zona": "Avellaneda",

                    "area": area,

                    "nivel": "",

                    "titulo": label,

                    "institucion": "",

                    "cargo": "",

                    "caracter": (
                        "Convocatoria / cobertura"
                    ),

                    "horas": "",

                    "fecha": "",

                    "estado": "Publicada",

                    "url": urljoin(
                        SAD_AVELLANEDA,
                        href
                    ),

                    "raw": label,
                })

        finally:

            await browser.close()

    unique = {}

    for item in results:

        unique[
            item["url"]
        ] = item

    return list(
        unique.values()
    )


# ============================================================
# EJECUTAR TODAS LAS FUENTES
# ============================================================

async def scrape_all():

    results = []

    source_status = {}

    # --------------------------------------------------------
    # CABA
    # --------------------------------------------------------

    try:

        caba_results = await scrape_caba()

        results.extend(
            caba_results
        )

        source_status["CABA"] = {

            "ok": True,

            "count": len(
                caba_results
            ),

        }

    except Exception as exc:

        source_status["CABA"] = {

            "ok": False,

            "count": 0,

            "error": repr(exc),

        }

    # --------------------------------------------------------
    # AVELLANEDA
    # --------------------------------------------------------

    try:

        avellaneda_results = (
            await scrape_avellaneda_public()
        )

        results.extend(
            avellaneda_results
        )

        source_status[
            "Avellaneda"
        ] = {

            "ok": True,

            "count": len(
                avellaneda_results
            ),

        }

    except Exception as exc:

        source_status[
            "Avellaneda"
        ] = {

            "ok": False,

            "count": 0,

            "error": repr(exc),

        }

    return (
        results,
        source_status
    )


# ============================================================
# RUTAS WEB
# ============================================================

@app.get("/")
async def home():

    return FileResponse(
        FRONTEND / "index.html"
    )


@app.get("/manifest.webmanifest")
async def manifest():

    return FileResponse(
        FRONTEND / "manifest.webmanifest",

        media_type=(
            "application/manifest+json"
        )
    )


@app.get("/sw.js")
async def service_worker():

    return FileResponse(
        FRONTEND / "sw.js",

        media_type=(
            "application/javascript"
        )
    )


# ============================================================
# API
# ============================================================

@app.get("/api/oportunidades")
async def oportunidades():

    return load_data()


@app.get("/api/salud")
async def salud():

    data = load_data()

    return {

        "ok": True,

        "checked_at": data.get(
            "checked_at"
        ),

        "items": len(
            data.get(
                "items",
                []
            )
        ),

        "source_status": data.get(
            "source_status",
            {}
        ),
    }


@app.post("/api/actualizar")
async def actualizar():

    database = load_data()

    # IDs que ya conocíamos.
    old_ids = {

        item.get("id")

        for item in database.get(
            "items",
            []
        )

        if item.get("id")

    }

    fresh, source_status = (
        await scrape_all()
    )

    now = datetime.now(
        timezone.utc
    ).isoformat()

    items = []

    for item in fresh:

        item["id"] = make_id(

            item.get(
                "source",
                ""
            ),

            item.get(
                "url",
                ""
            ),

            item.get(
                "titulo",
                ""
            ),

            item.get(
                "raw",
                ""
            ),

        )

        item["nueva"] = (
            item["id"]
            not in old_ids
        )

        items.append(
            item
        )

    data = {

        "checked_at": now,

        "items": items,

        "source_status":
            source_status,

    }

    save_data(data)

    return data
