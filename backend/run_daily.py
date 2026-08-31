import asyncio
from app import scrape_all, load, save, uid
from datetime import datetime, timezone

async def main():
    db=load()
    old={x.get("id") for x in db.get("items",[])}
    fresh=await scrape_all()
    items=[]
    for x in fresh:
        if "error" in x: continue
        x["id"]=uid(x.get("source",""),x.get("url",""),x.get("titulo",""),x.get("raw",""))
        x["nueva"]=x["id"] not in old
        items.append(x)
    save({"checked_at":datetime.now(timezone.utc).isoformat(),"items":items})

if __name__=="__main__": asyncio.run(main())
