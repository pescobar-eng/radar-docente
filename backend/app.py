import os
import json
import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

from fastapi import FastAPI, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
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

FRONTEND = Path("/app/frontend")

CABA_BASE = "https://actopublico.bue.edu.ar/"
SAD_AVELLANEDA = "https://www.sadavellaneda.com.ar/"


# ============================================================
# TÉRMINOS DE BÚSQUEDA
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
    return " ".join(
        (text or "").lower().split()
    )


def clean(text):
    return re.sub(
        r"\s+",
        " ",
        text or ""
    ).strip()


def load_data():

    if DATA.exists():

        try:

            return json.loads(
                DATA.read_text(
                    encoding="utf-8"
                )
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


def make_id(
    source,
    url,
    title,
    raw=""
):

    value = (
        f"{source}|"
        f"{url}|"
        f"{title}|"
        f"{raw}"
    )

    return hashlib.sha256(
        value.encode("utf-8")
    ).hexdigest()[:24]


# ============================================================
# CLASIFICACIÓN
# ============================================================

def classify(text):

    text = normalize(text)

    # Primero preceptoría.
    for word in PRECEPTORIA_WORDS:

        if word in text:
            return "Preceptoría"

    # Después danza.
    for word in DANZA_WORDS:

        if word in text:
            return "Danza"

    return None


# ============================================================
# EXTRACCIÓN DE CAMPOS
# ============================================================

def get_value(
    lines,
    labels
):

    """
    Busca valores en distintos formatos posibles.

    Ejemplo 1:

        Asignatura
        DANZA CLÁSICA

    Ejemplo 2:

        Asignatura: DANZA CLÁSICA

    Ejemplo 3:

        Asignatura:
        DANZA CLÁSICA
    """

    labels = [
        normalize(label)
        for label in labels
    ]

    for i, line in enumerate(lines):

        current = normalize(line)

        for label in labels:

            # ------------------------------------------------
            # Formato:
            # Asignatura: DANZA CLÁSICA
            # ------------------------------------------------

            prefix = label + ":"

            if current.startswith(prefix):

                value = (
                    line
                    .split(":", 1)[1]
                    .strip()
                )

                if value:
                    return value

                # La etiqueta está sola.
                # Buscamos el siguiente valor.
                j = i + 1

                while j < len(lines):

                    next_value = (
                        lines[j]
                        .strip()
                    )

                    if next_value:
                        return next_value

                    j += 1

            # ------------------------------------------------
            # Formato:
            #
            # Asignatura
            # DANZA CLÁSICA
            # ------------------------------------------------

            if current == label:

                j = i + 1

                while j < len(lines):

                    next_value = (
                        lines[j]
                        .strip()
                    )

                    if next_value:
                        return next_value

                    j += 1

    return ""


def extract_lines(text):

    return [
        clean(line)
        for line in text.splitlines()
        if clean(line)
    ]


# ============================================================
# SCRAPER CABA
# ============================================================

async def scrape_caba():

    results = []

    async with async_playwright() as p:

        browser = await p.chromium.launch(
            headless=True
        )

        seen_urls = set()

        # ----------------------------------------------------
        # Recorremos hasta 20 páginas del filtro ARTÍSTICA.
        # ----------------------------------------------------

        for page_number in range(1, 21):

            url = (
                CABA_BASE
                + "?areas%5B0%5D=8"
                + "&asignaturas%5B0%5D=0"
                + "&cargos%5B0%5D=0"
                + "&escuelas%5B0%5D=0"
                + "&especialidades%5B0%5D=0"
                + f"&page={page_number}"
                + "&status_carousel=1"
                + "&status_map=1"
            )

            page = await browser.new_page()

            try:

                await page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=60000
                )

                await page.wait_for_timeout(
                    5000
                )

                # Activamos contenido dinámico.
                for _ in range(8):

                    await page.mouse.wheel(
                        0,
                        1800
                    )

                    await page.wait_for_timeout(
                        400
                    )

                solicitudes = (
                    await page.locator(
                        'a[href*="/solicitud/"]'
                    ).evaluate_all(
                        """
                        elements => elements.map(
                            a => {

                                let node = a;
                                let block = "";

                                for (
                                    let i = 0;
                                    i < 10 && node;
                                    i++
                                ) {

                                    const text =
                                        (
                                            node.innerText ||
                                            ""
                                        ).trim();

                                    if (
                                        text.includes(
                                            "Estado:"
                                        )
                                        ||
                                        text.includes(
                                            "Tipo de acto:"
                                        )
                                        ||
                                        text.includes(
                                            "Asignatura:"
                                        )
                                    ) {

                                        block = text;
                                        break;
                                    }

                                    node =
                                        node.parentElement;
                                }

                                return {
                                    href: a.href,
                                    label:
                                        (
                                            a.innerText ||
                                            ""
                                        ).trim(),
                                    block: block
                                };
                            }
                        )
                        """
                    )
                )

            except Exception:

                await page.close()
                continue

            await page.close()

            # Si no hay solicitudes, terminamos.
            if not solicitudes:
                break

            # ------------------------------------------------
            # Procesar solicitudes.
            # ------------------------------------------------

            for solicitud in solicitudes:

                href = solicitud.get(
                    "href",
                    ""
                )

                label = clean(
                    solicitud.get(
                        "label",
                        ""
                    )
                )

                block = clean(
                    solicitud.get(
                        "block",
                        ""
                    )
                )

                if not href:
                    continue

                if href in seen_urls:
                    continue

                seen_urls.add(href)

                # ------------------------------------------------
                # Abrir ficha individual.
                # ------------------------------------------------

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

                    detail_text = (
                        await detail.locator(
                            "body"
                        ).inner_text()
                    )

                except Exception:

                    await detail.close()
                    continue

                await detail.close()

                # ------------------------------------------------
                # Convertir ficha a líneas.
                # ------------------------------------------------

                lines = extract_lines(
                    detail_text
                )

                # ------------------------------------------------
                # Extraer datos.
                # ------------------------------------------------

                estado = get_value(
                    lines,
                    [
                        "estado"
                    ]
                )

                tipo_acto = get_value(
                    lines,
                    [
                        "tipo de acto"
                    ]
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
                    [
                        "distrito"
                    ]
                )

                turno = get_value(
                    lines,
                    [
                        "turno"
                    ]
                )

                caracter = get_value(
                    lines,
                    [
                        "carácter",
                        "caracter"
                    ]
                )

                horas = get_value(
                    lines,
                    [
                        "horas cátedra",
                        "horas"
                    ]
                )

                nivel = get_value(
                    lines,
                    [
                        "nivel"
                    ]
                )

                fecha = get_value(
                    lines,
                    [
                        "fecha de acto público",
                        "fecha de acto",
                        "acto público"
                    ]
                )

                # ------------------------------------------------
                # FILTRO:
                # cargo + asignatura.
                # ------------------------------------------------

                datos = normalize(
                    f"{nombre_cargo} {asignatura}"
                )

                area = classify(
                    datos
                )

                # Si esos campos no alcanzan, usamos el listado
                # como respaldo.
                if area is None:

                    area = classify(
                        f"{label} {block}"
                    )

                # Si sigue sin ser Danza o Preceptoría,
                # descartamos.
                if area is None:
                    continue

                # ------------------------------------------------
                # ESTADO
                # ------------------------------------------------

                estado_normalizado = normalize(
                    estado
                )

                if estado_normalizado in [
                    "asignada",
                    "cerrada",
                    "desistida",
                    "baja",
                ]:
                    continue

                # ------------------------------------------------
                # Título.
                # ------------------------------------------------

                titulo = (
                    asignatura
                    or nombre_cargo
                    or especialidad
                    or label
                    or "Oportunidad docente"
                )

                # ------------------------------------------------
                # Guardamos.
                # ------------------------------------------------

                results.append({

                    "source":
                        "CABA",

                    "zona":
                        "CABA",

                    "area":
                        area,

                    "nivel":
                        nivel
                        or area_oficial,

                    "titulo":
                        titulo,

                    "institucion":
                        establecimiento,

                    "cargo":
                        (
                            asignatura
                            or nombre_cargo
                        ),

                    "caracter":
                        caracter,

                    "horas":
                        horas,

                    "fecha":
                        fecha,

                    "estado":
                        estado
                        or "Publicada",

                    "url":
                        href,

                    "turno":
                        turno,

                    "distrito":
                        distrito,

                    "especialidad":
                        especialidad,

                    "tipo_acto":
                        tipo_acto,

                    "raw":
                        detail_text[:7000],
                })

        await browser.close()

    # --------------------------------------------------------
    # Eliminar duplicados.
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

                    href = (
                        await link.get_attribute(
                            "href"
                        )
                    )

                except Exception:

                    continue

                if not label or not href:
                    continue

                area = classify(
                    label
                )

                if area is None:
                    continue

                text = normalize(
                    label
                )

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

                    "source":
                        "SAD Avellaneda",

                    "zona":
                        "Avellaneda",

                    "area":
                        area,

                    "nivel":
                        "",

                    "titulo":
                        label,

                    "institucion":
                        "",

                    "cargo":
                        "",

                    "caracter":
                        "Convocatoria / cobertura",

                    "horas":
                        "",

                    "fecha":
                        "",

                    "estado":
                        "Publicada",

                    "url":
                        urljoin(
                            SAD_AVELLANEDA,
                            href
                        ),

                    "raw":
                        label,
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
# SCRAPING GENERAL
# ============================================================

async def scrape_all():

    results = []

    source_status = {}

    # --------------------------------------------------------
    # CABA
    # --------------------------------------------------------

    try:

        caba_results = (
            await scrape_caba()
        )

        results.extend(
            caba_results
        )

        source_status[
            "CABA"
        ] = {

            "ok":
                True,

            "count":
                len(
                    caba_results
                ),
        }

    except Exception as exc:

        source_status[
            "CABA"
        ] = {

            "ok":
                False,

            "count":
                0,

            "error":
                repr(exc),
        }

    # --------------------------------------------------------
    # Avellaneda
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

            "ok":
                True,

            "count":
                len(
                    avellaneda_results
                ),
        }

    except Exception as exc:

        source_status[
            "Avellaneda"
        ] = {

            "ok":
                False,

            "count":
                0,

            "error":
                repr(exc),
        }

    return (
        results,
        source_status
    )


# ============================================================
# ACTUALIZACIÓN
# ============================================================

async def ejecutar_actualizacion():

    database = load_data()

    old_ids = {

        item.get("id")

        for item
        in database.get(
            "items",
            []
        )

        if item.get("id")

    }

    fresh, source_status = (
        await scrape_all()
    )

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

        "checked_at":
            datetime.now(
                timezone.utc
            ).isoformat(),

        "items":
            items,

        "source_status":
            source_status,
    }

    save_data(
        data
    )

    return data


# ============================================================
# WEB
# ============================================================

@app.get("/")
async def home():

    return FileResponse(
        FRONTEND / "index.html"
    )


@app.get(
    "/manifest.webmanifest"
)
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

@app.get(
    "/api/oportunidades"
)
async def oportunidades():

    return load_data()


@app.get(
    "/api/salud"
)
async def salud():

    data = load_data()

    return {

        "ok":
            True,

        "checked_at":
            data.get(
                "checked_at"
            ),

        "items":
            len(
                data.get(
                    "items",
                    []
                )
            ),

        "source_status":
            data.get(
                "source_status",
                {}
            ),
    }


# ============================================================
# ACTUALIZACIÓN MANUAL
#
# La usa el botón "Actualizar ahora".
#
# Esta ruta espera a que termine el scraping.
# ============================================================

@app.post(
    "/api/actualizar"
)
async def actualizar():

    return await ejecutar_actualizacion()


# ============================================================
# ACTUALIZACIÓN DIARIA
#
# Esta ruta la llama GitHub Actions.
#
# Responde inmediatamente y deja el scraping corriendo
# en segundo plano, evitando el timeout 502 de Railway.
# ============================================================

@app.post(
    "/api/actualizar-diario"
)
async def actualizar_diario(
    background_tasks: BackgroundTasks
):

    background_tasks.add_task(
        ejecutar_actualizacion
    )

    return {

        "ok":
            True,

        "status":
            "started",

        "message":
            "Actualización iniciada en segundo plano",
    }
