"""Live web evidence about each shortlisted provider via Tavily."""
import os
from concurrent.futures import ThreadPoolExecutor

from tavily import TavilyClient

_client = None


def client() -> TavilyClient:
    global _client
    if _client is None:
        _client = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))
    return _client


def provider_evidence(name: str, town: str) -> dict:
    """CQC inspection rating plus recent news for one provider."""
    out = {"cqc": [], "news": []}
    try:
        cqc = client().search(f"{name} {town} CQC inspection rating", max_results=3,
                              include_domains=["cqc.org.uk"], search_depth="advanced")
        out["cqc"] = [_trim(r) for r in cqc.get("results", [])]
    except Exception as e:
        out["error"] = str(e)
    try:
        news = client().search(f'"{name}" {town} hospital', topic="news", days=365, max_results=3,
                               exclude_domains=["facebook.com", "instagram.com", "x.com", "tiktok.com"])
        out["news"] = [_trim(r) for r in news.get("results", [])]
    except Exception as e:
        out["error"] = str(e)
    return out


def gather(providers: list[dict]) -> dict:
    with ThreadPoolExecutor(8) as pool:
        results = pool.map(lambda p: (p["code"], provider_evidence(p["name"], p["town"])), providers)
    return dict(results)


def _trim(r: dict) -> dict:
    return {"title": r.get("title", ""), "url": r.get("url", ""), "content": (r.get("content") or "")[:1200]}
