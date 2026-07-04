"""
ArchPi DuckDuckGo Text Search Agent
Provides web text search results as a secondary research source alongside Tavily.
Uses the `ddgs` package (maintained successor of `duckduckgo-search`, whose
old backends are rate-limited); falls back to the legacy package.
"""
import sys
import json

try:
    from ddgs import DDGS
except ImportError:  # legacy fallback
    from duckduckgo_search import DDGS


def text_search(query, max_results=5):
    """Perform a DuckDuckGo text search and return structured results."""
    try:
        with DDGS() as ddgs:
            try:
                results = ddgs.text(
                    query=query,
                    region="wt-wt",
                    safesearch="moderate",
                    max_results=max_results,
                )
            except TypeError:
                # legacy duckduckgo_search uses keywords=
                results = ddgs.text(keywords=query, max_results=max_results)
            if results:
                return [
                    {
                        "title": r.get("title", ""),
                        "url": r.get("href", r.get("link", "")),
                        "snippet": r.get("body", r.get("snippet", ""))
                    }
                    for r in results
                ]
    except Exception as e:
        return [{"error": str(e)}]
    return []


if __name__ == '__main__':
    try:
        if len(sys.argv) < 2:
            print(json.dumps({"error": "No queries provided"}))
            sys.exit(1)

        queries = json.loads(sys.argv[1])
        results = {}
        for q in queries:
            results[q] = text_search(q)

        print(json.dumps(results))
    except Exception as e:
        print(json.dumps({"error": str(e)}))
