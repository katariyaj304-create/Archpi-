"""
ArchPi Architectural Sketch Search Agent

Finds the best architectural elevation drawing / sketch of a building from the
internet, so the simulation page can paint an FEA stress heatmap onto a clean
line drawing instead of a photograph.

Search order:
1. Tavily API image search (TAVILY_API_KEY from environment)
2. DuckDuckGo image search (no key required)

Results are ranked so that elevation drawings, blueprints, and line sketches
come first; photos, interiors, and logos are pushed down.

Usage:  python sketch_agent.py "Eiffel Tower"
Output: JSON {"url": best, "all": [top candidates], "source": ...}
"""
import os
import sys
import json
import re
import time
import urllib.request
import urllib.parse

TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


def http_json(url, data=None, headers=None, timeout=20, retries=2):
    """GET/POST returning parsed JSON with small retry."""
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=data, headers=headers or {})
            resp = urllib.request.urlopen(req, timeout=timeout)
            return json.loads(resp.read().decode("utf-8", errors="ignore"))
        except Exception as e:
            last = e
            if attempt < retries - 1:
                time.sleep(1)
    raise last


def tavily_images(query, max_results=10):
    """Tavily search with images included."""
    if not TAVILY_API_KEY:
        return []
    body = json.dumps({
        "api_key": TAVILY_API_KEY,
        "query": query,
        "include_images": True,
        "search_depth": "basic",
        "max_results": max_results,
    }).encode("utf-8")
    data = http_json(
        "https://api.tavily.com/search",
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": UA},
    )
    images = data.get("images", []) or []
    # Tavily may return dicts {url, description} or plain strings
    urls = []
    for img in images:
        u = img.get("url") if isinstance(img, dict) else img
        if u:
            urls.append(u)
    return urls


def ddg_images(query, max_results=15):
    """DuckDuckGo image search (unofficial i.js endpoint)."""
    q = urllib.parse.quote(query)
    # Step 1: get the vqd token from the HTML search page
    req = urllib.request.Request(
        f"https://duckduckgo.com/?q={q}&iax=images&ia=images",
        headers={"User-Agent": UA},
    )
    html = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", errors="ignore")
    m = re.search(r"vqd=['\"]?([\d-]+)", html)
    if not m:
        return []
    vqd = m.group(1)
    # Step 2: query the image JSON endpoint
    data = http_json(
        f"https://duckduckgo.com/i.js?l=us-en&o=json&q={q}&vqd={vqd}&f=,,,&p=1",
        headers={"User-Agent": UA, "Referer": "https://duckduckgo.com/"},
    )
    return [r["image"] for r in data.get("results", [])[:max_results] if r.get("image")]


# Ranking: sketch/elevation indicators vs photo/noise indicators in the URL
SKETCH_HINTS = [
    "elevation", "drawing", "sketch", "blueprint", "diagram", "facade",
    "section", "line-art", "lineart", "outline", "cad", "vector",
    "illustration", "drawn", "architectural", "schematic", "dimensions",
    "detailed", "straight", "front-view", "front_view", "frontview",
]
BAD_HINTS = [
    "interior", "night", "aerial", "map", "logo", "photo", "sunset",
    "thumb/1px", "avatar", "icon",
    # multi-object sheets and wide scenes — a single centered structure is needed
    "panorama", "skyline", "background", "collection", "-set-", "pattern",
    "wallpaper", "banner", "landmarks",
    # tiny previews segment badly
    "260nw", "150nw", "-small", "thumbnail",
    # angled views distort the stress-height mapping — elevations only
    "perspective", "isometric", "3d-render", "angle",
]


def score_url(u):
    ul = u.lower()
    s = 0
    s += sum(3 for h in SKETCH_HINTS if h in ul)
    s -= sum(3 for h in BAD_HINTS if h in ul)
    if ul.endswith(".png"):
        s += 2  # drawings are commonly PNG on white
    if "dimensions." in ul:
        s += 4  # dimensions.com hosts clean elevation drawings
    if re.search(r"(600w|1200|1500|2000)", ul):
        s += 2  # larger renditions preprocess better
    return s


def upgrade_url(u):
    """Swap known tiny-preview markers for larger renditions of the same asset."""
    return u.replace("260nw", "600nw").replace("150nw", "600nw")


def usable(u):
    ul = u.lower()
    if ul.endswith((".svg", ".gif", ".webp", ".ico")):
        return False
    return ul.startswith("http")


def search_sketches(building, category=""):
    # A category from the AI profile ("fort", "cathedral", ...) disambiguates
    # buildings whose names alone are generic
    subject = f"{building} {category}".strip() if category and category.lower() not in building.lower() else building
    queries = [
        f"{subject} architectural elevation drawing black and white",
        f"{subject} architecture sketch line drawing",
    ]
    urls, seen = [], set()
    for q in queries:
        for engine in (tavily_images, ddg_images):
            try:
                for u in engine(q):
                    u = upgrade_url(u)
                    if u and u not in seen and usable(u):
                        seen.add(u)
                        urls.append(u)
            except Exception:
                pass
        if len(urls) >= 12:
            break
    urls.sort(key=score_url, reverse=True)
    return urls[:8]


if __name__ == "__main__":
    try:
        if len(sys.argv) < 2:
            print(json.dumps({"error": "No building name provided"}))
            sys.exit(1)
        name = sys.argv[1]
        category = sys.argv[2] if len(sys.argv) > 2 else ""
        results = search_sketches(name, category)
        print(json.dumps({
            "url": results[0] if results else "",
            "all": results,
            "source": "sketch-search",
            "query": name,
        }))
    except Exception as e:
        print(json.dumps({"error": str(e), "url": "", "all": []}))
