"""
ArchPi Batch Image Search Agent
Takes a JSON array of search queries on argv[1] and returns a JSON object
mapping each query -> best image URL (plus "<query>__all" -> list of
candidate URLs for fallback rotation).

Uses DuckDuckGo image search via the `ddgs` package (the maintained
successor of `duckduckgo-search`, whose old backends are rate-limited).
Falls back to the legacy package if `ddgs` is unavailable.
"""
import sys
import json
import time

try:
    from ddgs import DDGS
except ImportError:  # legacy fallback
    from duckduckgo_search import DDGS


def image_search(ddgs, query, max_results=4):
    """Return a list of image URLs for a query, best-first."""
    urls = []
    try:
        results = ddgs.images(
            query=query,
            region="wt-wt",
            safesearch="moderate",
            max_results=max_results,
        )
        for r in results or []:
            url = r.get("image") or r.get("thumbnail") or ""
            if url and url.startswith("http") and url not in urls:
                urls.append(url)
    except TypeError:
        # legacy duckduckgo_search uses keywords= instead of query=
        try:
            results = ddgs.images(keywords=query, max_results=max_results)
            for r in results or []:
                url = r.get("image") or r.get("thumbnail") or ""
                if url and url.startswith("http") and url not in urls:
                    urls.append(url)
        except Exception as e:
            print(f"legacy image search failed for '{query}': {e}", file=sys.stderr)
    except Exception as e:
        print(f"image search failed for '{query}': {e}", file=sys.stderr)
    return urls


if __name__ == "__main__":
    try:
        if len(sys.argv) < 2:
            print(json.dumps({"error": "No queries provided"}))
            sys.exit(1)

        queries = json.loads(sys.argv[1])
        if isinstance(queries, str):
            queries = [queries]

        out = {}
        with DDGS() as ddgs:
            for i, q in enumerate(queries):
                urls = image_search(ddgs, q)
                if not urls:
                    # one retry after a longer pause — late queries in a big
                    # batch occasionally come back empty from DDG
                    time.sleep(1.5)
                    urls = image_search(ddgs, q)
                out[q] = urls[0] if urls else ""
                out[q + "__all"] = urls
                # gentle pacing to avoid DDG rate limiting on big batches
                if i < len(queries) - 1:
                    time.sleep(0.4)

        print(json.dumps(out))
    except Exception as e:
        print(json.dumps({"error": str(e)}))
