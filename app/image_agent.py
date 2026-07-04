"""
ArchPi Building Image Search Agent
Fetches architectural elevation images of buildings from Wikipedia/Wikimedia.

Strategy:
1. Look up the Wikipedia article by exact title
2. Get all images in the article
3. Fetch image metadata (dimensions, URL)
4. Prefer tall-aspect-ratio images (elevation/front views of buildings)
5. Return the best image URL for CAD stencil preprocessing
"""
import sys
import json
import time
import urllib.request
import urllib.parse
import re


USER_AGENT = "ArchPi/1.0 (Architectural Simulation Tool)"


def fetch_json(url, retries=3):
    """Fetch JSON from URL, retrying with backoff on transient/rate-limit errors."""
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
            })
            resp = urllib.request.urlopen(req, timeout=15)
            return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(0.5 * (attempt + 1) * 2)
    raise last_err


def get_wikipedia_title(building_name):
    """Resolve a building name to its exact Wikipedia article title."""
    try:
        q = urllib.parse.quote(building_name)
        # Use opensearch which is simpler and more reliable than CirrusSearch
        url = f"https://en.wikipedia.org/w/api.php?action=opensearch&search={q}&limit=1&format=json"
        data = fetch_json(url)
        # opensearch returns: [query, [titles], [descriptions], [urls]]
        if len(data) > 1 and data[1]:
            return data[1][0]
    except:
        pass
    # Fallback: use building_name with underscores
    return building_name.replace(" ", "_")


def get_article_images(title):
    """Get all image filenames from a Wikipedia article."""
    try:
        t = urllib.parse.quote(title)
        url = f"https://en.wikipedia.org/w/api.php?action=query&titles={t}&prop=images&format=json&imlimit=50"
        data = fetch_json(url)
        if data.get("query") and data["query"].get("pages"):
            pages = data["query"]["pages"]
            page = list(pages.values())[0]
            return [img["title"] for img in page.get("images", [])]
    except:
        pass
    return []


def filter_relevant_images(image_titles, building_name):
    """Filter image titles to find relevant building photos."""
    relevant = []
    skip_patterns = [
        r'\.svg$', r'\.ogv$', r'\.ogg$', r'\.webm$',  # Non-image formats
        r'logo', r'icon', r'flag', r'coat.of.arms',    # UI elements
        r'commons-logo', r'edit-ltr', r'protection',   # Wiki UI
        r'symbol', r'bsicon', r'p_parthenon',          # Icons
        r'portrait', r'signature', r'stamp', r'medal', # People/memorabilia,
        r'banknote', r'grave', r'tomb', r'plaque',     # not the structure itself
        r'bust\b', r'nadar', r'daguerr',
    ]
    
    # Building-name-related keywords for prioritization
    name_parts = building_name.lower().split()
    
    for title in image_titles:
        title_lower = title.lower()
        
        # Skip non-image and UI files
        if any(re.search(pat, title_lower) for pat in skip_patterns):
            continue
        
        # Only include image files
        if not re.search(r'\.(jpg|jpeg|png|gif|webp)$', title_lower):
            continue
        
        # Score: how many building name words appear in the filename
        score = sum(1 for part in name_parts if part in title_lower)
        relevant.append((title, score))
    
    # Sort by relevance score (descending)
    relevant.sort(key=lambda x: x[1], reverse=True)
    return [r[0] for r in relevant]


def get_image_info(image_titles, thumb_width=1200):
    """Get image URLs and dimensions for a list of image titles."""
    if not image_titles:
        return []
    
    # API supports up to 50 titles at once
    titles_str = "|".join(image_titles[:20])
    t = urllib.parse.quote(titles_str)
    url = (
        f"https://en.wikipedia.org/w/api.php?"
        f"action=query&titles={t}"
        f"&prop=imageinfo&iiprop=url|size|mime"
        f"&iiurlwidth={thumb_width}&format=json"
    )
    
    try:
        data = fetch_json(url)
        results = []
        if data.get("query") and data["query"].get("pages"):
            for page in data["query"]["pages"].values():
                info_list = page.get("imageinfo", [])
                if not info_list:
                    continue
                info = info_list[0]
                w = info.get("width", 0)
                h = info.get("height", 0)
                mime = info.get("mime", "")
                
                if not mime.startswith("image/") or "svg" in mime:
                    continue
                
                results.append({
                    "title": page.get("title", ""),
                    "url": info.get("thumburl", info.get("url", "")),
                    "full_url": info.get("url", ""),
                    "width": w,
                    "height": h,
                    "aspect": h / max(w, 1),
                    "mime": mime,
                })
        return results
    except:
        return []


def get_main_page_image(title, thumb_width=1200):
    """Get the main page image directly (works even when search is down)."""
    try:
        t = urllib.parse.quote(title)
        url = f"https://en.wikipedia.org/w/api.php?action=query&titles={t}&prop=pageimages&format=json&pithumbsize={thumb_width}"
        data = fetch_json(url)
        if data.get("query") and data["query"].get("pages"):
            page = list(data["query"]["pages"].values())[0]
            if page.get("thumbnail"):
                return {
                    "url": page["thumbnail"]["source"],
                    "width": page["thumbnail"].get("width", 0),
                    "height": page["thumbnail"].get("height", 0),
                }
    except:
        pass
    return None


def search_building_image(building_name):
    """Find the best architectural image of a building."""
    # Step 1: Resolve Wikipedia title
    wiki_title = get_wikipedia_title(building_name)
    
    # Step 2: Get all images from the article
    image_titles = get_article_images(wiki_title)
    
    # Step 3: Filter for relevant building images
    relevant = filter_relevant_images(image_titles, building_name)
    
    # Step 4: Get image metadata
    image_infos = get_image_info(relevant)
    
    all_urls = []

    # Step 5: The article's lead image goes FIRST, but only if it is a tall/portrait
    # frame — a tall lead image of a building article is almost always the structure
    # itself (elevation view), while a wide one is usually a skyline panorama that
    # segments badly. Wide lead images are kept as a late fallback instead.
    main_img = get_main_page_image(wiki_title)
    main_is_tall = bool(main_img and main_img["url"]
                        and main_img.get("height", 0) > main_img.get("width", 1))
    if main_is_tall:
        all_urls.append(main_img["url"])

    if image_infos:
        # Rank article images: filename must mention the building (relevance),
        # then prefer tall aspect ratios (elevation/front views)
        name_parts = building_name.lower().split()

        def rank(info):
            fname = info["title"].lower()
            relevance = sum(1 for part in name_parts if part in fname)
            return (relevance > 0, min(info["aspect"], 3.0))

        image_infos.sort(key=rank, reverse=True)

        for info in image_infos:
            url = info["url"]
            if url and url not in all_urls:
                all_urls.append(url)

    # Wide lead image goes last — better than nothing if all else fails
    if main_img and main_img["url"] and not main_is_tall and main_img["url"] not in all_urls:
        all_urls.append(main_img["url"])
    
    if all_urls:
        return {
            "url": all_urls[0],
            "all": all_urls[:5],
            "wiki_title": wiki_title,
            "source": "wikipedia",
            "total_found": len(image_infos),
        }
    
    return {"url": "", "all": [], "source": "none", "wiki_title": wiki_title}


if __name__ == "__main__":
    try:
        if len(sys.argv) < 2:
            print(json.dumps({"error": "No building name provided"}))
            sys.exit(1)
        
        building_name = sys.argv[1]
        result = search_building_image(building_name)
        print(json.dumps(result))
    except Exception as e:
        print(json.dumps({"error": str(e)}))
