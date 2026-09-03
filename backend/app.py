import os
import json
import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

from fastapi import FastAPI, BackgroundTasks
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

# Si Railway no permite escribir /data, usamos una carpeta local.
FALLBACK_DATA = Path(__file__).resolve().parent / "data.json"

FRONTEND = Path("/app/frontend")

CABA_BASE = "https://actopublico.bue.edu.ar/"
SAD_AVELLANEDA = "https://www.sadavellaneda.com.ar/"

# Portal APD de Provincia de Buenos Aires.
APD_PBA = "https://servicios.abc.gov.ar/actos.publicos.digitales/"


# ============================================================
# CONFIGURACIÓN DEL SCRAPER
# ============================================================

# Máxima cantidad de páginas que recorreremos.
#
# NO significa que solamente haya que recorrer esta cantidad.
# El scraper intenta descubrir la cantidad real de páginas.
#
# Este límite funciona como protección ante cambios del sitio.
MAX_CABA_PAGES = 100

# Tiempo de espera de navegación.
PAGE_TIMEOUT = 60000

# Espera para que cargue Javascript.
DYNAMIC_WAIT = 2500


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
    "educación artística - música",
    "educacion artistica - musica",
    "educación artística - artes visuales",
    "educacion artistica - artes visuales",
    "artes visuales",
    "artes plásticas",
    "artes plasticas",
    "música",
    "musica",
    "teatro",
    "artes",
    "artístico",
    "artistico",
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
# UTILIDADES GENERALES
# ============================================================

def normalize(text):
    """
    Normaliza texto para poder comparar sin problemas
    de mayúsculas, acentos, espacios, etc.
    """

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
    """
    Limpia espacios y saltos de línea.
    """

    return re.sub(
        r"\s+",
        " ",
        text or ""
    ).strip()


def extract_lines(text):
    """
    Convierte el texto de una página en líneas limpias.
    """

    lines = []

    for line in (text or "").splitlines():

        value = clean(line)

        if value:
            lines.append(value)

    return lines


def now_iso():
    return datetime.now(
        timezone.utc
    ).isoformat()


# ============================================================
# PERSISTENCIA
# ============================================================

def get_data_file():
    """
    Usa /data/data.json en Railway.

    Si no puede utilizarse, usa backend/data.json.
    """

    try:

        DATA.parent.mkdir(
            parents=True,
            exist_ok=True
        )

        test_file = DATA.parent / ".write_test"

        test_file.write_text(
            "ok",
            encoding="utf-8"
        )

        test_file.unlink(
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

            if not isinstance(data, dict):
                raise ValueError(
                    "El archivo de datos no contiene un objeto JSON."
                )

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

    temp_path = path.with_suffix(
        ".tmp"
    )

    temp_path.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )

    temp_path.replace(path)


# ============================================================
# ID ESTABLE
# ============================================================

def make_id(
    source,
    url,
    title=""
):
    """
    El ID se basa principalmente en la URL oficial.

    Esto es MUY importante:
    si cambian las horas o algún dato de una oferta,
    sigue siendo la misma oferta y no aparece como una
    oferta completamente nueva.
    """

    value = (
        f"{source}|"
        f"{url}|"
        f"{title}"
    )

    return hashlib.sha256(
        value.encode("utf-8")
    ).hexdigest()[:24]


# ============================================================
# CLASIFICACIÓN
# ============================================================

def classify(
    cargo="",
    asignatura="",
    area_oficial="",
    especialidad="",
    institucion="",
    raw=""
):
    """
    Determina si una oferta pertenece a:

    - Danza
    - Educación Artística
    - Preceptoría

    IMPORTANTE:
    No clasificamos solamente por el nombre de la escuela.
    Eso generaba falsos positivos.

    Se priorizan:
    cargo
    asignatura
    área
    especialidad
    y solamente como último respaldo el texto completo.
    """

    cargo_n = normalize(cargo)
    asignatura_n = normalize(asignatura)
    area_n = normalize(area_oficial)
    especialidad_n = normalize(especialidad)

    # --------------------------------------------------------
    # PRECEPTORÍA
    # --------------------------------------------------------

    for word in PRECEPTORIA_WORDS:

        word_n = normalize(word)

        if (
            word_n in cargo_n
            or word_n in asignatura_n
        ):
            return "Preceptoría"

    # --------------------------------------------------------
    # DANZA
    # --------------------------------------------------------

    for word in DANZA_WORDS:

        word_n = normalize(word)

        if (
            word_n in cargo_n
            or word_n in asignatura_n
            or word_n in especialidad_n
        ):
            return "Danza"

    # --------------------------------------------------------
    # EDUCACIÓN ARTÍSTICA
    # --------------------------------------------------------

    for word in EDUCACION_ARTISTICA_WORDS:

        word_n = normalize(word)

        if (
            word_n in cargo_n
            or word_n in asignatura_n
            or word_n in especialidad_n
        ):
            return "Educación Artística"

    # Área oficial ARTISTICA.
    if area_n == "artistica":
        return "Educación Artística"

    # --------------------------------------------------------
    # RESPALDO FINAL
    # --------------------------------------------------------

    fallback = normalize(
        f"{cargo} "
        f"{asignatura} "
        f"{area_oficial} "
        f"{especialidad} "
        f"{institucion}"
    )

    for word in DANZA_WORDS:

        if normalize(word) in fallback:
            return "Danza"

    for word in PRECEPTORIA_WORDS:

        if normalize(word) in fallback:
            return "Preceptoría"

    # Solamente usamos el raw como último recurso.
    raw_n = normalize(raw)

    for word in DANZA_WORDS:

        if normalize(word) in raw_n:
            return "Danza"

    for word in PRECEPTORIA_WORDS:

        if normalize(word) in raw_n:
            return "Preceptoría"

    return None


# ============================================================
# EXTRACCIÓN ROBUSTA DE CAMPOS
# ============================================================

def get_value(
    lines,
    labels
):
    """
    Extrae valores aunque el sitio utilice cualquiera de estos
    formatos:

        Asignatura: DANZA

    o:

        Asignatura:
        DANZA

    o:

        Asignatura

        DANZA
    """

    normalized_labels = [
        normalize(label)
        for label in labels
    ]

    for i, line in enumerate(lines):

        current = normalize(line)

        for label in normalized_labels:

            # ------------------------------------------------
            # Etiqueta + valor en la misma línea
            # ------------------------------------------------

            prefix = label + ":"

            if current.startswith(prefix):

                value = line.split(
                    ":",
                    1
                )[1].strip()

                if value:
                    return value

                # Si después de ":" no hay nada,
                # buscamos la siguiente línea no vacía.
                for j in range(
                    i + 1,
                    min(
                        len(lines),
                        i + 8
                    )
                ):

                    candidate = lines[j].strip()

                    if candidate:
                        return candidate

            # ------------------------------------------------
            # Etiqueta sola
            # ------------------------------------------------

            if current == label:

                for j in range(
                    i + 1,
                    min(
                        len(lines),
                        i + 8
                    )
                ):

                    candidate = lines[j].strip()

                    if candidate:
                        return candidate

    return ""


def get_hours(lines):
    """
    Busca horas cátedra y también contempla variantes.
    """

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

    # Respaldo mediante regex.
    text = " ".join(lines)

    match = re.search(
        r"horas\s+c[aá]tedra\s*:?\s*(\d+(?:[.,]\d+)?)",
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
# EXTRAER LINKS DE UNA PÁGINA CABA
# ============================================================

async def extract_caba_links(page):
    """
    Obtiene todos los enlaces /solicitud/ de una página.

    No depende de una clase CSS específica.
    """

    links = await page.locator(
        'a[href*="/solicitud/"]'
    ).evaluate_all(
        """
        elements => elements.map(a => ({
            href: a.href,
            text: (a.innerText || "").trim()
        }))
        """
    )

    result = []

    seen = set()

    for item in links:

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
# DESCUBRIR CANTIDAD DE PÁGINAS CABA
# ============================================================

async def discover_caba_pages(page):
    """
    Intenta descubrir la última página del listado.

    Si el sitio no expone correctamente la paginación,
    seguimos hasta MAX_CABA_PAGES pero paramos cuando
    encontremos páginas sin solicitudes.
    """

    pages = set()

    # La página actual siempre existe.
    pages.add(1)

    try:

        pagination_links = await page.locator(
            "a"
        ).evaluate_all(
            """
            elements => elements.map(a => ({
                href: a.href || "",
                text: (a.innerText || "").trim()
            }))
            """
        )

        for link in pagination_links:

            href = link.get(
                "href",
                ""
            )

            text = link.get(
                "text",
                ""
            )

            # Intentamos obtener ?page=N.
            match = re.search(
                r"[?&]page=(\d+)",
                href
            )

            if match:

                number = int(
                    match.group(1)
                )

                if (
                    1 <= number
                    <= MAX_CABA_PAGES
                ):
                    pages.add(number)

            # También aceptamos botones numerados.
            if text.isdigit():

                number = int(text)

                if (
                    1 <= number
                    <= MAX_CABA_PAGES
                ):
                    pages.add(number)

    except Exception:
        pass

    # Si no encontró paginación, empezamos igualmente
    # con una cantidad razonable.
    if len(pages) == 1:
        return list(
            range(
                1,
                MAX_CABA_PAGES + 1
            )
        )

    return sorted(pages)


# ============================================================
# LEER UNA FICHA INDIVIDUAL CABA
# ============================================================

async def read_caba_detail(
    browser,
    href,
    listing_text=""
):
    """
    Abre la ficha individual y obtiene todos los campos.
    """

    page = await browser.new_page()

    try:

        await page.goto(
            href,
            wait_until="domcontentloaded",
            timeout=PAGE_TIMEOUT
        )

        await page.wait_for_timeout(
            800
        )

        detail_text = await page.locator(
            "body"
        ).inner_text()

        lines = extract_lines(
            detail_text
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
        # CLASIFICACIÓN
        # ----------------------------------------------------

        categoria = classify(
            cargo=nombre_cargo,
            asignatura=asignatura,
            area_oficial=area_oficial,
            especialidad=especialidad,
            institucion=establecimiento,
            raw=detail_text
        )

        if categoria is None:

            await page.close()

            return None

        # ----------------------------------------------------
        # ESTADOS QUE NO QUEREMOS MOSTRAR
        # ----------------------------------------------------

        estado_n = normalize(
            estado
        )

        estados_no_disponibles = {
            "asignada",
            "cerrada",
            "desistida",
            "baja",
            "anulada",
        }

        if estado_n in estados_no_disponibles:

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
                href,
                titulo
            ),

            "source": "CABA",

            "zona": "CABA",

            "area": categoria,

            "nivel": (
                nivel
                or area_oficial
                or ""
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

            # Conservamos una parte del texto original para
            # poder depurar el scraper si CABA cambia.
            "raw": detail_text[:10000],
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

        # ----------------------------------------------------
        # Abrimos primero la página 1.
        # ----------------------------------------------------

        first_page = await browser.new_page()

        try:

            first_url = (
                CABA_BASE
                + "?page=1"
            )

            await first_page.goto(
                first_url,
                wait_until="domcontentloaded",
                timeout=PAGE_TIMEOUT
            )

            await first_page.wait_for_timeout(
                DYNAMIC_WAIT
            )

            pages_to_check = (
                await discover_caba_pages(
                    first_page
                )
            )

            first_links = (
                await extract_caba_links(
                    first_page
                )
            )

        except Exception as exc:

            await first_page.close()
            await browser.close()

            raise RuntimeError(
                f"No se pudo leer CABA: {exc}"
            )

        await first_page.close()

        # ----------------------------------------------------
        # Si no encontramos paginación confiable,
        # iremos secuencialmente hasta encontrar una página
        # sin resultados.
        # ----------------------------------------------------

        seen_urls = set()

        # Procesamos primero página 1.
        pages_sequence = list(
            pages_to_check
        )

        # Aseguramos que haya 1.
        if 1 not in pages_sequence:
            pages_sequence.insert(
                0,
                1
            )

        # ----------------------------------------------------
        # Recorrer páginas
        # ----------------------------------------------------

        for page_number in pages_sequence:

            if (
                page_number < 1
                or page_number > MAX_CABA_PAGES
            ):
                continue

            # Ya tenemos links de página 1.
            if page_number == 1:

                links = first_links

            else:

                page = await browser.new_page()

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

                except Exception:

                    stats["errors"] += 1

                    await page.close()

                    continue

                await page.close()

            stats["pages_checked"] += 1

            stats["links_found"] += len(
                links
            )

            # ------------------------------------------------
            # Si la página está vacía y estamos recorriendo
            # secuencialmente, no tiene sentido continuar.
            # ------------------------------------------------

            if not links:

                # Solamente cortamos si estamos más allá
                # de la primera página.
                if page_number > 1:
                    break

                continue

            # ------------------------------------------------
            # Procesar fichas
            # ------------------------------------------------

            for link in links:

                href = link.get(
                    "href",
                    ""
                )

                listing_text = link.get(
                    "text",
                    ""
                )

                if not href:
                    continue

                if href in seen_urls:
                    continue

                seen_urls.add(
                    href
                )

                stats[
                    "details_checked"
                ] += 1

                item = await read_caba_detail(
                    browser,
                    href,
                    listing_text
                )

                if item is None:
                    continue

                results.append(
                    item
                )

                stats[
                    "relevant"
                ] += 1

        await browser.close()

    # --------------------------------------------------------
    # DEDUPLICACIÓN
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

async def scrape_avellaneda_public():

    """
    Avellaneda / Provincia:

    NO inventamos ofertas.

    La página pública de SAD Avellaneda sirve para
    comunicaciones, cronogramas y accesos, pero las
    ofertas APD pueden requerir autenticación ABC.

    Por eso esta función solamente devuelve información
    pública verificable y deja claramente indicado el estado.
    """

    results = []

    stats = {
        "public_page_ok": False,
        "apd_public_ok": False,
        "login_required": False,
        "links_found": 0,
    }

    async with async_playwright() as p:

        browser = await p.chromium.launch(
            headless=True
        )

        # ----------------------------------------------------
        # Página SAD Avellaneda
        # ----------------------------------------------------

        page = await browser.new_page()

        try:

            await page.goto(
                SAD_AVELLANEDA,
                wait_until="domcontentloaded",
                timeout=PAGE_TIMEOUT
            )

            await page.wait_for_timeout(
                2000
            )

            body = await page.locator(
                "body"
            ).inner_text()

            stats[
                "public_page_ok"
            ] = True

            # ------------------------------------------------
            # Buscamos enlaces relevantes.
            # ------------------------------------------------

            links = await page.locator(
                "a"
            ).evaluate_all(
                """
                elements => elements.map(a => ({
                    href: a.href || "",
                    text: (a.innerText || "").trim()
                }))
                """
            )

            stats[
                "links_found"
            ] = len(
                links
            )

            # No agregamos una página de SAD como si fuera
            # una oferta docente individual.
            #
            # Esto evita mostrar información engañosa.

        except Exception:

            body = ""

        await page.close()

        # ----------------------------------------------------
        # Intento de comprobar APD público.
        # ----------------------------------------------------

        apd_page = await browser.new_page()

        try:

            await apd_page.goto(
                APD_PBA,
                wait_until="domcontentloaded",
                timeout=PAGE_TIMEOUT
            )

            await apd_page.wait_for_timeout(
                2000
            )

            apd_text = await apd_page.locator(
                "body"
            ).inner_text()

            apd_n = normalize(
                apd_text
            )

            # Si aparece login / CUIL / contraseña,
            # consideramos que el acceso a las ofertas
            # está protegido.
            if (
                "cuil" in apd_n
                or "contraseña" in apd_n
                or "contrasena" in apd_n
                or "iniciar sesion" in apd_n
                or "iniciar sesión" in apd_n
            ):

                stats[
                    "login_required"
                ] = True

            else:

                stats[
                    "apd_public_ok"
                ] = True

        except Exception:

            # Si no podemos acceder, no inventamos nada.
            stats[
                "login_required"
            ] = True

        await apd_page.close()

        await browser.close()

    return results, stats


# ============================================================
# NORMALIZACIÓN DE ITEMS ANTIGUOS
# ============================================================

def normalize_existing_item(item):
    """
    Mantiene compatibilidad con datos de versiones anteriores.
    """

    item = dict(item)

    item.setdefault(
        "id",
        make_id(
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
                item.get(
                    "title",
                    ""
                )
            )
        )
    )

    # Compatibilidad entre versiones.
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

    if not item.get("url"):
        item["url"] = ""

    return item


# ============================================================
# ACTUALIZACIÓN PRINCIPAL
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

    # --------------------------------------------------------
    # IDs que ya habíamos visto.
    # --------------------------------------------------------

    seen_before = {
        item.get("id")
        for item in previous_items
        if item.get("id")
    }

    # --------------------------------------------------------
    # SCRAPE CABA
    # --------------------------------------------------------

    caba_error = None

    try:

        caba_items, caba_stats = (
            await scrape_caba()
        )

        caba_ok = True

    except Exception as exc:

        caba_items = []

        caba_stats = {
            "error": str(exc)
        }

        caba_error = str(
            exc
        )

        caba_ok = False

    # --------------------------------------------------------
    # SCRAPE AVELLANEDA
    # --------------------------------------------------------

    avellaneda_error = None

    try:

        av_items, av_stats = (
            await scrape_avellaneda_public()
        )

        av_ok = bool(
            av_stats.get(
                "public_page_ok",
                False
            )
        )

    except Exception as exc:

        av_items = []

        av_stats = {
            "error": str(exc)
        }

        avellaneda_error = str(
            exc
        )

        av_ok = False

    # --------------------------------------------------------
    # UNIFICAR
    # --------------------------------------------------------

    current_items = (
        caba_items
        + av_items
    )

    # --------------------------------------------------------
    # NUEVA
    # --------------------------------------------------------

    for item in current_items:

        item["nueva"] = (
            item.get("id")
            not in seen_before
        )

    # --------------------------------------------------------
    # IMPORTANTE:
    #
    # NO conservamos ofertas viejas que ya no aparecen.
    #
    # La lista representa las ofertas que actualmente
    # aparecen en la fuente.
    # --------------------------------------------------------

    # Orden:
    # nuevas primero
    # después por fecha / título.
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
    # Estado de las fuentes
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

            "error": avellaneda_error,

        },
    }

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
# ENDPOINT PRINCIPAL DE ACTUALIZACIÓN
# ============================================================

@app.post(
    "/api/actualizar"
)
async def actualizar():

    try:

        data = await perform_update()

        return JSONResponse(
            content={
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
        )

    except Exception as exc:

        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "error": str(exc),
            }
        )


# ============================================================
# ENDPOINT DE DATOS
# ============================================================

@app.get(
    "/api/posts"
)
async def posts():

    data = load_data()

    items = [
        normalize_existing_item(item)
        for item in data.get(
            "items",
            []
        )
    ]

    # Más recientes / nuevas primero.
    items.sort(
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
# FILTROS
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

    areas = sorted({
        item.get(
            "nivel",
            ""
        )
        for item in items
        if item.get(
            "nivel",
            ""
        )
    })

    sources = sorted({
        item.get(
            "source",
            ""
        )
        for item in items
        if item.get(
            "source",
            ""
        )
    })

    return {

        "ok": True,

        "locations": locations,

        "categories": categories,

        "areas": areas,

        "sources": sources,
    }


# ============================================================
# HEALTH / SALUD
# ============================================================

@app.get(
    "/api/health"
)
@app.get(
    "/api/salud"
)
async def health():

    data = load_data()

    source_status = data.get(
        "source_status",
        {}
    )

    caba = source_status.get(
        "CABA",
        {}
    )

    avellaneda = source_status.get(
        "Avellaneda",
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

        "source_status": {

            "CABA": caba,

            "Avellaneda": avellaneda,
        },
    }


# ============================================================
# INFORMACIÓN
# ============================================================

@app.get(
    "/api"
)
async def api_info():

    return {

        "app": "Radar Docente",

        "version": "2.0",

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
            "manual mediante POST /api/actualizar"
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

        Path("/app/Interfaz/index.html"),

        Path("/app/frontend/index.html"),

        Path(__file__).resolve().parent
        / "index.html",
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
            )
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
