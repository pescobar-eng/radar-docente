import os
import json
import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from playwright.async_api import async_playwright


# ============================================================
# CONFIGURACIÓN
# ============================================================

DATA = Path(
    os.getenv(
        "DATA_FILE",
        "/data/data.json"
    )
)

FALLBACK_DATA = (
    Path(__file__).resolve().parent / "data.json"
)

FRONTEND = Path("/app/frontend")

CABA_BASE = (
    "https://actopublico.bue.edu.ar/"
)

SAD_AVELLANEDA = (
    "https://www.sadavellaneda.com.ar/"
)

APD_PBA = (
    "https://servicios.abc.gov.ar/"
    "actos.publicos.digitales/"
)

MAX_CABA_PAGES = 100

PAGE_TIMEOUT = 60000

# No hace falta esperar varios segundos en cada página.
DYNAMIC_WAIT = 700


# ============================================================
# TÉRMINOS
# ============================================================

DANZA_WORDS = [
    "danza",
    "danzas",
    "danza clásica",
    "danza clasica",
    "danza contemporánea",
    "danza contemporanea",
    "danzas folklóricas",
    "danzas folkloricas",
    "danzas folclóricas",
    "danzas folcloricas",
    "tango",
    "expresión corporal",
    "expresion corporal",
    "ballet",
    "zapateo",
    "malambo",
]


PRECEPTORIA_WORDS = [
    "preceptor",
    "preceptora",
    "preceptoría",
    "preceptoria",
    "jefe de preceptores",
    "jefa de preceptores",
]


EDUCACION_ARTISTICA_WORDS = [
    "educación artística",
    "educacion artistica",
    "artes visuales",
    "artes plásticas",
    "artes plasticas",
    "música",
    "musica",
    "teatro",
    "artes",
    "artístico",
    "artistico",
    "plástica",
    "plastica",
]


# Materias que NO deben entrar como Educación Artística
# aunque la institución tenga Área ARTISTICA.

NON_ARTISTIC_SUBJECTS = [
    "matemática",
    "matematica",
    "matemáticas",
    "matematicas",
    "física",
    "fisica",
    "química",
    "quimica",
    "biología",
    "biologia",
    "lengua",
    "literatura",
    "inglés",
    "ingles",
    "francés",
    "frances",
    "historia",
    "geografía",
    "geografia",
    "economía",
    "economia",
    "contabilidad",
    "informática",
    "informatica",
    "computación",
    "computacion",
    "ciencias naturales",
    "ciencias sociales",
]


# ============================================================
# APLICACIÓN
# ============================================================

app = FastAPI(
    title="Radar Docente API"
)

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
    text = text or ""

    replacements = {
        "á": "a",
        "é": "e",
        "í": "i",
        "ó": "o",
        "ú": "u",
        "ü": "u",
        "ñ": "n",
    }

    text = text.lower()

    for old, new in replacements.items():
        text = text.replace(old, new)

    return " ".join(text.split())


def clean(text):
    return re.sub(
        r"\s+",
        " ",
        text or ""
    ).strip()


def extract_lines(text):
    result = []

    for line in (text or "").splitlines():
        line = clean(line)

        if line:
            result.append(line)

    return result


def now_iso():
    return datetime.now(
        timezone.utc
    ).isoformat()


# ============================================================
# DATOS
# ============================================================

def get_data_file():

    try:

        DATA.parent.mkdir(
            parents=True,
            exist_ok=True
        )

        test = DATA.parent / ".write_test"

        test.write_text(
            "ok",
            encoding="utf-8"
        )

        test.unlink(
            missing_ok=True
        )

        return DATA

    except Exception:

        FALLBACK_DATA.parent.mkdir(
            parents=True,
            exist_ok=True
        )

        return FALLBACK_DATA


def load_data():

    path = get_data_file()

    if path.exists():

        try:

            data = json.loads(
                path.read_text(
                    encoding="utf-8"
                )
            )

            if isinstance(data, dict):

                data.setdefault(
                    "checked_at",
                    None
                )

                data.setdefault(
                    "items",
                    []
                )

                data.setdefault(
                    "source_status",
                    {}
                )

                return data

        except Exception:
            pass

    return {
        "checked_at": None,
        "items": [],
        "source_status": {},
    }


def save_data(data):

    path = get_data_file()

    path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    tmp = path.with_suffix(
        ".tmp"
    )

    tmp.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )

    tmp.replace(path)


# ============================================================
# ID ESTABLE
# ============================================================

def make_id(source, url):

    value = (
        f"{source}|{url}"
    )

    return hashlib.sha256(
        value.encode("utf-8")
    ).hexdigest()[:24]


# ============================================================
# CLASIFICACIÓN
# ============================================================

def contains_any(text, words):

    text = normalize(text)

    for word in words:

        if normalize(word) in text:
            return True

    return False


def classify(
    cargo="",
    asignatura="",
    area_oficial="",
    especialidad=""
):

    cargo_n = normalize(cargo)
    asignatura_n = normalize(asignatura)
    area_n = normalize(area_oficial)
    especialidad_n = normalize(especialidad)

    # --------------------------------------------------------
    # PRECEPTORÍA
    # --------------------------------------------------------

    if contains_any(
        cargo_n,
        PRECEPTORIA_WORDS
    ):
        return "Preceptoría"

    if contains_any(
        asignatura_n,
        PRECEPTORIA_WORDS
    ):
        return "Preceptoría"

    # --------------------------------------------------------
    # DANZA
    # --------------------------------------------------------

    if contains_any(
        cargo_n,
        DANZA_WORDS
    ):
        return "Danza"

    if contains_any(
        asignatura_n,
        DANZA_WORDS
    ):
        return "Danza"

    if contains_any(
        especialidad_n,
        DANZA_WORDS
    ):
        return "Danza"

    # --------------------------------------------------------
    # EDUCACIÓN ARTÍSTICA
    # --------------------------------------------------------

    artistic_text = " ".join([
        cargo_n,
        asignatura_n,
        especialidad_n,
    ])

    if contains_any(
        artistic_text,
        NON_ARTISTIC_SUBJECTS
    ):

        # Si es claramente una materia no artística,
        # no la clasificamos como Educación Artística.
        return None

    if contains_any(
        artistic_text,
        EDUCACION_ARTISTICA_WORDS
    ):
        return "Educación Artística"

    # Área oficial ARTISTICA.
    #
    # Solamente la usamos si no encontramos una materia
    # claramente no artística.
    if area_n == "artistica":
        return "Educación Artística"

    return None


# ============================================================
# EXTRACCIÓN DE CAMPOS
# ============================================================

def get_value(lines, labels):

    labels_n = [
        normalize(label)
        for label in labels
    ]

    for i, line in enumerate(lines):

        current = normalize(line)

        for label in labels_n:

            # ------------------------------------------------
            # Etiqueta y valor en la misma línea
            # ------------------------------------------------

            if current.startswith(
                label + ":"
            ):

                value = line.split(
                    ":",
                    1
                )[1].strip()

                if value:
                    return value

                # Valor en la línea siguiente
                if i + 1 < len(lines):

                    return lines[i + 1]

            # ------------------------------------------------
            # Etiqueta sola
            # ------------------------------------------------

            if current == label:

                if i + 1 < len(lines):

                    return lines[i + 1]

    return ""


def get_hours(lines):

    value = get_value(
        lines,
        [
            "horas cátedra",
            "horas catedra",
            "horas",
            "cantidad de horas",
        ]
    )

    if value:
        return value

    text = " ".join(lines)

    match = re.search(
        r"horas\s+c[aá]tedra\s*:?\s*"
        r"(\d+(?:[.,]\d+)?)",
        text,
        flags=re.IGNORECASE
    )

    if match:
        return match.group(1)

    return ""


def get_fecha(lines):

    return get_value(
        lines,
        [
            "fecha de acto público",
            "fecha de acto publico",
            "fecha de acto",
        ]
    )


# ============================================================
# CABA — LINKS
# ============================================================

async def extract_caba_links(page):

    elements = await page.locator(
        'a[href*="/solicitud/"]'
    ).evaluate_all(
        """
        elements => elements.map(a => ({
            href: a.href || "",
            text: (a.innerText || "").trim()
        }))
        """
    )

    result = []

    seen = set()

    for item in elements:

        href = clean(
            item.get(
                "href",
                ""
            )
        )

        text = clean(
            item.get(
                "text",
                ""
            )
        )

        if not href:
            continue

        if "/solicitud/" not in href:
            continue

        if href in seen:
            continue

        seen.add(href)

        result.append({
            "href": href,
            "text": text,
        })

    return result


# ============================================================
# CABA — DESCUBRIR PAGINACIÓN
# ============================================================

async def discover_caba_last_page(page):

    max_page = 1

    try:

        elements = await page.locator(
            "a"
        ).evaluate_all(
            """
            elements => elements.map(a => ({
                href: a.href || "",
                text: (a.innerText || "").trim()
            }))
            """
        )

        for item in elements:

            href = item.get(
                "href",
                ""
            )

            text = item.get(
                "text",
                ""
            )

            match = re.search(
                r"[?&]page=(\d+)",
                href
            )

            if match:

                number = int(
                    match.group(1)
                )

                if number > max_page:
                    max_page = number

            if text.isdigit():

                number = int(text)

                if number > max_page:
                    max_page = number

    except Exception:
        pass

    return min(
        max_page,
        MAX_CABA_PAGES
    )


# ============================================================
# CABA — DETALLE
# ============================================================

async def read_caba_detail(
    context,
    href,
    listing_text=""
):

    page = await context.new_page()

    try:

        await page.goto(
            href,
            wait_until="domcontentloaded",
            timeout=PAGE_TIMEOUT
        )

        await page.wait_for_timeout(
            300
        )

        body = await page.locator(
            "body"
        ).inner_text()

        lines = extract_lines(
            body
        )

        estado = get_value(
            lines,
            ["estado"]
        )

        tipo_acto = get_value(
            lines,
            ["tipo de acto"]
        )

        area_oficial = get_value(
            lines,
            [
                "área",
                "area"
            ]
        )

        nombre_cargo = get_value(
            lines,
            [
                "nombre del cargo"
            ]
        )

        asignatura = get_value(
            lines,
            [
                "asignatura"
            ]
        )

        especialidad = get_value(
            lines,
            [
                "especialidad"
            ]
        )

        establecimiento = get_value(
            lines,
            [
                "establecimiento del cargo",
                "establecimiento"
            ]
        )

        distrito = get_value(
            lines,
            ["distrito"]
        )

        turno = get_value(
            lines,
            ["turno"]
        )

        caracter = get_value(
            lines,
            [
                "carácter",
                "caracter"
            ]
        )

        nivel = get_value(
            lines,
            ["nivel"]
        )

        horas = get_hours(
            lines
        )

        fecha = get_fecha(
            lines
        )

        # ----------------------------------------------------
        # ESTADO
        # ----------------------------------------------------

        estado_n = normalize(
            estado
        )

        unavailable = [
            "asignada",
            "cerrada",
            "desistida",
            "baja",
            "anulada",
        ]

        if estado_n in unavailable:

            await page.close()

            return None

        # ----------------------------------------------------
        # CLASIFICACIÓN
        # ----------------------------------------------------

        categoria = classify(
            cargo=nombre_cargo,
            asignatura=asignatura,
            area_oficial=area_oficial,
            especialidad=especialidad,
        )

        if categoria is None:

            await page.close()

            return None

        # ----------------------------------------------------
        # TÍTULO
        # ----------------------------------------------------

        titulo = (
            asignatura
            or nombre_cargo
            or especialidad
            or listing_text
            or "Oportunidad docente"
        )

        item = {

            "id": make_id(
                "CABA",
                href
            ),

            "source": "CABA",

            "zona": "CABA",

            "area": categoria,

            "nivel": clean(
                nivel
            ),

            "titulo": clean(
                titulo
            ),

            "institucion": clean(
                establecimiento
            ),

            "cargo": clean(
                nombre_cargo
                or asignatura
            ),

            "asignatura": clean(
                asignatura
            ),

            "caracter": clean(
                caracter
            ),

            "horas": clean(
                horas
            ),

            "fecha": clean(
                fecha
            ),

            "estado": clean(
                estado
                or "Publicada"
            ),

            "turno": clean(
                turno
            ),

            "distrito": clean(
                distrito
            ),

            "especialidad": clean(
                especialidad
            ),

            "tipo_acto": clean(
                tipo_acto
            ),

            "url": href,

            "scraped_at": now_iso(),

            "raw": body[:10000],
        }

        await page.close()

        return item

    except Exception:

        try:
            await page.close()
        except Exception:
            pass

        return None


# ============================================================
# SCRAPER CABA
# ============================================================

async def scrape_caba():

    results = []

    stats = {
        "pages_checked": 0,
        "links_found": 0,
        "details_checked": 0,
        "relevant": 0,
        "errors": 0,
    }

    async with async_playwright() as p:

        browser = await p.chromium.launch(
            headless=True
        )

        context = await browser.new_context()

        # ----------------------------------------------------
        # PRIMERA PÁGINA
        # ----------------------------------------------------

        first_page = await context.new_page()

        try:

            await first_page.goto(
                CABA_BASE + "?page=1",
                wait_until="domcontentloaded",
                timeout=PAGE_TIMEOUT
            )

            await first_page.wait_for_timeout(
                DYNAMIC_WAIT
            )

            first_links = (
                await extract_caba_links(
                    first_page
                )
            )

            last_page = (
                await discover_caba_last_page(
                    first_page
                )
            )

        except Exception as exc:

            await first_page.close()
            await context.close()
            await browser.close()

            raise RuntimeError(
                f"No se pudo leer CABA: {exc}"
            )

        await first_page.close()

        # ----------------------------------------------------
        # TODAS LAS PÁGINAS
        # ----------------------------------------------------

        all_links = []

        # Página 1
        all_links.extend(
            first_links
        )

        stats["pages_checked"] += 1

        stats["links_found"] += len(
            first_links
        )

        # Páginas restantes
        for page_number in range(
            2,
            last_page + 1
        ):

            page = await context.new_page()

            try:

                url = (
                    CABA_BASE
                    + f"?page={page_number}"
                )

                await page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=PAGE_TIMEOUT
                )

                await page.wait_for_timeout(
                    DYNAMIC_WAIT
                )

                links = (
                    await extract_caba_links(
                        page
                    )
                )

                stats["pages_checked"] += 1

                stats["links_found"] += len(
                    links
                )

                all_links.extend(
                    links
                )

            except Exception:

                stats["errors"] += 1

            finally:

                await page.close()

        # ----------------------------------------------------
        # LINKS ÚNICOS
        # ----------------------------------------------------

        unique_links = {}

        for link in all_links:

            href = link.get(
                "href",
                ""
            )

            if href:
                unique_links[href] = link

        # ----------------------------------------------------
        # LEER DETALLES
        # ----------------------------------------------------

        for href, link in unique_links.items():

            stats["details_checked"] += 1

            item = await read_caba_detail(
                context,
                href,
                link.get(
                    "text",
                    ""
                )
            )

            if item is None:
                continue

            results.append(
                item
            )

            stats["relevant"] += 1

        await context.close()

        await browser.close()

    # --------------------------------------------------------
    # DEDUPLICAR
    # --------------------------------------------------------

    unique = {}

    for item in results:

        unique[
            item["url"]
        ] = item

    results = list(
        unique.values()
    )

    stats["unique_relevant"] = len(
        results
    )

    return results, stats


# ============================================================
# AVELLANEDA
# ============================================================

async def scrape_avellaneda():

    results = []

    stats = {
        "public_page_ok": False,
        "apd_public_ok": False,
        "login_required": False,
    }

    async with async_playwright() as p:

        browser = await p.chromium.launch(
            headless=True
        )

        # ----------------------------------------------------
        # SAD AVELLANEDA
        # ----------------------------------------------------

        page = await browser.new_page()

        try:

            await page.goto(
                SAD_AVELLANEDA,
                wait_until="domcontentloaded",
                timeout=PAGE_TIMEOUT
            )

            await page.wait_for_timeout(
                1000
            )

            stats[
                "public_page_ok"
            ] = True

        except Exception:
            pass

        await page.close()

        # ----------------------------------------------------
        # APD PROVINCIA
        # ----------------------------------------------------

        apd = await browser.new_page()

        try:

            await apd.goto(
                APD_PBA,
                wait_until="domcontentloaded",
                timeout=PAGE_TIMEOUT
            )

            await apd.wait_for_timeout(
                1000
            )

            text = await apd.locator(
                "body"
            ).inner_text()

            text_n = normalize(
                text
            )

            if (
                "cuil" in text_n
                or "contrasena" in text_n
                or "contraseña" in text_n
                or "iniciar sesion" in text_n
                or "iniciar sesión" in text_n
            ):

                stats[
                    "login_required"
                ] = True

            else:

                stats[
                    "apd_public_ok"
                ] = True

        except Exception:

            stats[
                "login_required"
            ] = True

        await apd.close()

        await browser.close()

    return results, stats


# ============================================================
# COMPATIBILIDAD CON DATOS ANTERIORES
# ============================================================

def normalize_existing_item(item):

    item = dict(item)

    if not item.get("url"):
        item["url"] = ""

    if not item.get("titulo"):
        item["titulo"] = item.get(
            "title",
            ""
        )

    if not item.get("zona"):
        item["zona"] = item.get(
            "location",
            ""
        )

    if not item.get("area"):
        item["area"] = item.get(
            "category",
            ""
        )

    if not item.get("horas"):
        item["horas"] = item.get(
            "hours",
            ""
        )

    if not item.get("fecha"):
        item["fecha"] = item.get(
            "date",
            ""
        )

    if not item.get("id"):

        item["id"] = make_id(
            item.get(
                "source",
                ""
            ),
            item.get(
                "url",
                ""
            )
        )

    return item


# ============================================================
# ACTUALIZACIÓN
# ============================================================

async def perform_update():

    previous = load_data()

    previous_items = [
        normalize_existing_item(item)
        for item in previous.get(
            "items",
            []
        )
    ]

    seen_before = {
        item.get("id")
        for item in previous_items
        if item.get("id")
    }

    # --------------------------------------------------------
    # CABA
    # --------------------------------------------------------

    try:

        caba_items, caba_stats = (
            await scrape_caba()
        )

        caba_ok = True
        caba_error = None

    except Exception as exc:

        caba_items = []

        caba_stats = {
            "error": str(exc)
        }

        caba_ok = False
        caba_error = str(exc)

    # --------------------------------------------------------
    # AVELLANEDA
    # --------------------------------------------------------

    try:

        av_items, av_stats = (
            await scrape_avellaneda()
        )

        av_ok = bool(
            av_stats.get(
                "public_page_ok",
                False
            )
        )

        av_error = None

    except Exception as exc:

        av_items = []

        av_stats = {
            "error": str(exc)
        }

        av_ok = False
        av_error = str(exc)

    # --------------------------------------------------------
    # UNIFICAR
    # --------------------------------------------------------

    current_items = (
        caba_items
        + av_items
    )

    # --------------------------------------------------------
    # MARCAR NUEVAS
    # --------------------------------------------------------

    for item in current_items:

        item["nueva"] = (
            item.get("id")
            not in seen_before
        )

    # --------------------------------------------------------
    # ORDEN
    # --------------------------------------------------------

    current_items.sort(
        key=lambda item: (
            not item.get(
                "nueva",
                False
            ),
            normalize(
                item.get(
                    "titulo",
                    ""
                )
            )
        )
    )

    # --------------------------------------------------------
    # ESTADO DE FUENTES
    # --------------------------------------------------------

    source_status = {

        "CABA": {

            "ok": caba_ok,

            "count": len(
                caba_items
            ),

            "details": caba_stats,

            "error": caba_error,
        },

        "Avellaneda": {

            "ok": av_ok,

            "count": len(
                av_items
            ),

            "details": av_stats,

            "error": av_error,
        },
    }

    # --------------------------------------------------------
    # SEGURIDAD:
    # si CABA falló completamente, NO borramos los datos
    # anteriores.
    #
    # Esto evita que un error temporal del sitio deje
    # el Radar vacío.
    # --------------------------------------------------------

    if not caba_ok:

        old_caba = [
            item
            for item in previous_items
            if item.get(
                "zona"
            ) == "CABA"
        ]

        current_items = (
            old_caba
            + av_items
        )

        for item in current_items:
            item["nueva"] = False

    # --------------------------------------------------------
    # GUARDAR
    # --------------------------------------------------------

    data = {

        "checked_at": now_iso(),

        "items": current_items,

        "source_status": source_status,
    }

    save_data(
        data
    )

    return data


# ============================================================
# API — ACTUALIZAR
# ============================================================

@app.post(
    "/api/actualizar"
)
async def actualizar():

    try:

        data = await perform_update()

        # IMPORTANTE:
        # el frontend necesita recibir los ITEMS,
        # no solamente la cantidad.

        return JSONResponse(
            content={
                "ok": True,

                "checked_at": data.get(
                    "checked_at"
                ),

                "items": data.get(
                    "items",
                    []
                ),

                "source_status": data.get(
                    "source_status",
                    {}
                ),
            }
        )

    except Exception as exc:

        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "error": str(exc),
                "items": [],
            }
        )


# ============================================================
# API — OPORTUNIDADES
# ============================================================

@app.get(
    "/api/oportunidades"
)
async def oportunidades():

    data = load_data()

    items = [
        normalize_existing_item(item)
        for item in data.get(
            "items",
            []
        )
    ]

    return {

        "ok": True,

        "checked_at": data.get(
            "checked_at"
        ),

        "items": items,

        "source_status": data.get(
            "source_status",
            {}
        ),
    }


# ============================================================
# API — POSTS
# ============================================================

@app.get(
    "/api/posts"
)
async def posts():

    return await oportunidades()


# ============================================================
# API — FILTROS
# ============================================================

@app.get(
    "/api/filters"
)
async def filters():

    data = load_data()

    items = [
        normalize_existing_item(item)
        for item in data.get(
            "items",
            []
        )
    ]

    locations = sorted({
        item.get(
            "zona",
            ""
        )
        for item in items
        if item.get(
            "zona",
            ""
        )
    })

    categories = sorted({
        item.get(
            "area",
            ""
        )
        for item in items
        if item.get(
            "area",
            ""
        )
    })

    return {

        "ok": True,

        "locations": locations,

        "categories": categories,
    }


# ============================================================
# API — HEALTH
# ============================================================

@app.get(
    "/api/health"
)
@app.get(
    "/api/salud"
)
async def health():

    data = load_data()

    status = data.get(
        "source_status",
        {}
    )

    caba = status.get(
        "CABA",
        {}
    )

    return {

        "ok": bool(
            caba.get(
                "ok",
                False
            )
        ),

        "checked_at": data.get(
            "checked_at"
        ),

        "items": len(
            data.get(
                "items",
                []
            )
        ),

        "source_status": status,
    }


# ============================================================
# API — INFORMACIÓN
# ============================================================

@app.get(
    "/api"
)
async def api_info():

    return {

        "app": "Radar Docente",

        "version": "3.0",

        "sources": [
            "CABA",
            "Avellaneda",
        ],

        "categories": [
            "Danza",
            "Educación Artística",
            "Preceptoría",
        ],

        "update": (
            "manual mediante "
            "POST /api/actualizar"
        ),
    }


# ============================================================
# FRONTEND
# ============================================================

@app.get(
    "/"
)
async def home():

    possible_files = [

        FRONTEND / "index.html",

        Path(
            "/app/frontend/index.html"
        ),

        Path(
            "/app/Interfaz/index.html"
        ),

        (
            Path(__file__).resolve().parent
            / "index.html"
        ),
    ]

    for file_path in possible_files:

        if file_path.exists():

            return FileResponse(
                file_path
            )

    return JSONResponse(
        content={
            "ok": True,
            "message": (
                "Radar Docente API funcionando."
            ),
        }
    )


# ============================================================
# ARRANQUE
# ============================================================

if __name__ == "__main__":

    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(
            os.getenv(
                "PORT",
                "8000"
            )
        )
    )
