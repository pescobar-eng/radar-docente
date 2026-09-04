import asyncio
from datetime import datetime, timezone

from app import scrape_all, load_data, save_data, make_id


async def main():
    print("======================================")
    print("RADAR DOCENTE - ACTUALIZACIÓN DIARIA")
    print("======================================")

    database = load_data()

    old_ids = {
        item.get("id")
        for item in database.get("items", [])
        if item.get("id")
    }

    old_items = database.get("items", [])

    print("Consultando CABA y Avellaneda...")

    fresh, source_status = await scrape_all()

    # Fuentes que respondieron correctamente
    healthy_sources = {
        source
        for source, status in source_status.items()
        if isinstance(status, dict) and status.get("ok") is True
    }

    items = []

    # Conservamos los resultados anteriores de las fuentes
    # que hayan fallado en esta actualización.
    for old_item in old_items:
        source = old_item.get("source", "")

        if source not in healthy_sources:
            items.append(old_item)

    # Agregamos los resultados nuevos de las fuentes que funcionaron.
    for item in fresh:
        source = item.get("source", "")

        # Solo incorporamos resultados de fuentes saludables.
        if source not in healthy_sources:
            continue

        item["id"] = make_id(
            item.get("source", ""),
            item.get("url", "")
        )

        item["nueva"] = item["id"] not in old_ids

        items.append(item)

    data = {
        "checked_at": datetime.now(
            timezone.utc
        ).isoformat(),

        "items": items,

        "source_status": source_status,
    }

    save_data(data)

    print("")
    print("RESULTADO")
    print("--------------------------------------")

    print(
        "CABA:",
        source_status.get(
            "CABA",
            {}
        ).get(
            "count",
            0
        )
    )

    print(
        "Avellaneda:",
        source_status.get(
            "Avellaneda",
            {}
        ).get(
            "count",
            0
        )
    )

    print(
        "Nuevas:",
        sum(
            1
            for item in items
            if item.get("nueva")
        )
    )

    print(
        "Fecha:",
        data["checked_at"]
    )

    print("======================================")


if __name__ == "__main__":
    asyncio.run(main())
