import re

import httpx

from ..config import settings
from ..openrouter import chat_json


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


async def find_statements(company: dict, country: str, emit) -> list[dict]:
    """AI call #2: find annual report / financial statement PDFs for the last
    3 years and download them locally. Returns [{"year", "url", "path"}]."""
    name = company["name"]
    await emit("find_statements", "running", f"Searching for financial statement PDFs for {name}...")
    candidates = await chat_json(
        settings.model_find_statements,
        [
            {"role": "system", "content": (
                "You find direct download links to official annual reports / audited "
                "financial statements (PDF files). Respond ONLY with a JSON array, no prose."
            )},
            {"role": "user", "content": (
                f'Find direct PDF download URLs for the annual reports / financial statements '
                f'of "{name}" ({country}) for the last 3 fiscal years. '
                'Respond with a JSON array: [{"year": 2024, "url": "https://...pdf", "title": "..."}]. '
                "Only include URLs that point directly at a PDF file. If you find nothing, return []."
            )},
        ],
    )
    if not isinstance(candidates, list) or not candidates:
        await emit("find_statements", "error", "Model returned no PDF candidates")
        return []

    await emit("find_statements", "running",
               f"Model proposed {len(candidates)} PDF(s), downloading...", {"candidates": candidates})

    dest_dir = settings.download_dir / _slug(name)
    dest_dir.mkdir(parents=True, exist_ok=True)
    downloaded: list[dict] = []
    async with httpx.AsyncClient(follow_redirects=True, timeout=60,
                                 headers={"User-Agent": "Mozilla/5.0 (asb-finstat hackathon)"}) as client:
        for item in candidates[:6]:
            url = item.get("url", "")
            year = item.get("year", "unknown")
            try:
                resp = await client.get(url)
                resp.raise_for_status()
                if not resp.content.startswith(b"%PDF"):
                    await emit("find_statements", "running", f"Skipping {url} - not a PDF")
                    continue
                title_slug = _slug(str(item.get("title") or "report"))[:60]
                path = dest_dir / f"{year}_{title_slug}.pdf"
                path.write_bytes(resp.content)
                downloaded.append({"year": year, "url": url, "path": path})
                await emit("find_statements", "running",
                           f"Downloaded {path.name} ({len(resp.content) // 1024} KB)")
            except Exception as e:
                await emit("find_statements", "running", f"Failed to download {url}: {e}")

    status = "done" if downloaded else "error"
    await emit("find_statements", status, f"{len(downloaded)} PDF(s) downloaded to {dest_dir}",
               {"files": [str(d["path"]) for d in downloaded]})
    return downloaded
