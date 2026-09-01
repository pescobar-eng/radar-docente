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

    print("Consultando CABA y Avellaneda...")

    fresh, source_status = await scrape_all()

    items = []

    for item in fresh:

        item["id"] = make_id(
            item.get("source", ""),
            item.get("url", ""),
            item.get("titulo", ""),
            item.get("raw", "")
        )

        item["nueva"] = (
            item["id"] not in old_ids
        )

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
    print("CABA:",
          source_status.get(
              "CABA",
              {}
          ).get(
              "count",
              0
          ))

    print("Avellaneda:",
          source_status.get(
              "Avellaneda",
              {}
          ).get(
              "count",
              0
          ))

    print("Nuevas:",
          sum(
              1
              for item in items
              if item.get("nueva")
          ))

    print("Fecha:",
          data["checked_at"])

    print("======================================")


if __name__ == "__main__":
    asyncio.run(main())
