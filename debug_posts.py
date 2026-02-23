import asyncio
import json
from app.database import get_pool, fetch_all

async def main():
    await get_pool()
    rows = await fetch_all("SELECT id, url, text, source, project_id FROM posts LIMIT 10;")
    for r in rows:
        txt = (r["text"] or "")[:200]
        print(json.dumps({
            "id": str(r["id"]),
            "url": r["url"],
            "text_preview": txt,
            "text_len": len(r["text"] or ""),
            "source": r["source"],
            "project_id": str(r["project_id"]) if r["project_id"] else None,
        }, ensure_ascii=False))

asyncio.run(main())
