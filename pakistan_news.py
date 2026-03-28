"""
pakistan_news.py
Fetches Pakistan-focused news from English-language RSS feeds, categorizes
stories, and writes them to docs/pakistan_news.json — capped at 20 per
category, max age 7 days, oldest entries replaced first.
No external APIs are used. All sources publish in English.
"""

import json
import os
import re
import time
import logging
from datetime import datetime, timezone, timedelta
from dateutil import parser as dateparser
import feedparser

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

OUTPUT_DIR = "docs"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "pakistan_news.json")
MAX_PER_CATEGORY = 20
MAX_AGE_DAYS = 7
CATEGORIES = ["Diplomacy", "Military", "Energy", "Economy", "Local Events"]

# RSS feeds — all free, English-language, Pakistan-focused, no APIs
FEEDS = [
    # Dawn — Pakistan's oldest and most influential English newspaper
    {"source": "Dawn", "url": "https://www.dawn.com/feeds/home"},
    {"source": "Dawn", "url": "https://www.dawn.com/pakistan"},
    {"source": "Dawn", "url": "https://www.dawn.com/business"},
    {"source": "Dawn", "url": "https://www.dawn.com/world"},
    # The News International — Pakistan's largest English daily
    {"source": "The News International", "url": "https://www.thenews.com.pk/rss/1/1"},
    {"source": "The News International", "url": "https://www.thenews.com.pk/rss/2/6"},
    {"source": "The News International", "url": "https://www.thenews.com.pk/rss/2/7"},
    # The Express Tribune — English, affiliated with International NYT
    {"source": "The Express Tribune", "url": "https://tribune.com.pk/feed"},
    {"source": "The Express Tribune", "url": "https://tribune.com.pk/feed/pakistan"},
    {"source": "The Express Tribune", "url": "https://tribune.com.pk/feed/business"},
    # Geo News — Pakistan's most-watched news network (English digital)
    {"source": "Geo News", "url": "https://www.geo.tv/rss/top-stories"},
    {"source": "Geo News", "url": "https://www.geo.tv/rss/pakistan"},
    {"source": "Geo News", "url": "https://www.geo.tv/rss/business"},
    # Arab News Pakistan — English-language Pakistan edition
    {"source": "Arab News Pakistan", "url": "https://www.arabnews.pk/rss.xml"},
]

# ---------------------------------------------------------------------------
# Category keyword mapping (Pakistan-contextualised)
# ---------------------------------------------------------------------------

CATEGORY_KEYWORDS = {
    "Diplomacy": [
        "diplomacy", "diplomatic", "foreign policy", "embassy", "ambassador",
        "treaty", "bilateral", "multilateral", "united nations", "sco",
        "foreign minister", "foreign affairs", "summit", "sanctions",
        "international relations", "geopolitical", "saarc", "oic",
        "trade deal", "g20", "accord", "alliance", "envoy", "consul",
        "ishaq dar", "bilawal", "foreign office", "ministry of foreign",
        "pakistan and india", "pakistan and china", "pakistan and us",
        "pakistan and afghanistan", "cpec", "belt and road",
        "pak-us", "pak-china", "pak-india", "kashmir", "un security",
    ],
    "Military": [
        "military", "army", "navy", "air force", "pak army", "ispr",
        "defence", "defense", "troops", "soldier", "weapons", "missile",
        "nuclear", "armed forces", "war", "combat", "deployment",
        "conflict", "bomb", "intelligence", "isi", "terrorism",
        "terrorist", "ttp", "balochistan insurgency", "counterterrorism",
        "operation", "ieds", "border skirmish", "line of control",
        "loc", "general", "chief of army", "coas", "asim munir",
        "fighter jet", "drone", "explosion", "militant", "attack",
    ],
    "Energy": [
        "energy", "oil", "gas", "petroleum", "pso", "ogdcl",
        "renewable", "solar", "wind", "electricity", "power grid",
        "load shedding", "loadshedding", "blackout", "power outage",
        "net zero", "carbon", "climate", "fossil fuel", "emissions",
        "coal", "hydro", "hydroelectric", "tarbela", "mangla",
        "lng", "pipeline", "energy crisis", "power sector",
        "nepra", "fuel price", "petrol price", "diesel price",
        "pakistan energy", "power plant", "cpec energy",
    ],
    "Economy": [
        "economy", "economic", "gdp", "inflation", "interest rate",
        "state bank", "imf", "world bank", "budget", "finance minister",
        "tax", "unemployment", "jobs", "recession", "growth", "trade",
        "rupee", "pkr", "psx", "karachi stock", "fiscal", "spending",
        "debt", "deficit", "wage", "cost of living", "investment",
        "business", "exports", "imports", "remittances", "current account",
        "forex", "reserves", "sbp", "ishaq dar", "aurangzeb",
        "privatisation", "privatization", "agriculture", "textile",
        "manufacturing", "retail", "imc", "economic crisis",
    ],
    "Local Events": [
        "local", "province", "provincial", "chief minister", "governor",
        "city", "town", "district", "community", "hospital", "school",
        "crime", "police", "court", "flood", "earthquake", "fire",
        "transport", "strike", "protest", "housing", "karachi",
        "lahore", "islamabad", "rawalpindi", "peshawar", "quetta",
        "multan", "faisalabad", "hyderabad", "gwadar", "sialkot",
        "punjab", "sindh", "khyber pakhtunkhwa", "kpk", "balochistan",
        "gilgit baltistan", "azad kashmir", "election", "by-election",
        "pti", "pmln", "ppp", "pakistan tehreek", "imran khan",
        "nawaz sharif", "zardari", "violence", "lynching", "blasphemy",
        "flood relief", "disaster", "ngo", "civil society",
    ],
}


def classify(title: str, description: str):
    """Return the best-matching category for a story, or None if no match."""
    text = (title + " " + (description or "")).lower()
    scores = {cat: 0 for cat in CATEGORIES}
    for cat, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if re.search(r'\b' + re.escape(kw) + r'\b', text):
                scores[cat] += 1
    best_cat = max(scores, key=scores.get)
    return best_cat if scores[best_cat] > 0 else None


def strip_html(text: str) -> str:
    """Remove HTML tags from a string."""
    return re.sub(r"<[^>]+>", "", text or "").strip()


def parse_date(entry):
    """Parse a feed entry's published date into a UTC-aware datetime."""
    raw = entry.get("published") or entry.get("updated") or entry.get("created")
    if not raw:
        struct = entry.get("published_parsed") or entry.get("updated_parsed")
        if struct:
            return datetime(*struct[:6], tzinfo=timezone.utc)
        return None
    try:
        dt = dateparser.parse(raw)
        if dt and dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc) if dt else None
    except Exception:
        return None


def fetch_feed(feed_cfg: dict) -> list:
    """Fetch a single RSS feed and return a list of story dicts."""
    source = feed_cfg["source"]
    url = feed_cfg["url"]
    stories = []
    try:
        parsed = feedparser.parse(url)
        if parsed.bozo and not parsed.entries:
            log.warning("Bozo feed (%s): %s", source, url)
            return stories
        cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)
        for entry in parsed.entries:
            pub_date = parse_date(entry)
            if pub_date and pub_date < cutoff:
                continue
            title = strip_html(entry.get("title", "")).strip()
            desc = strip_html(entry.get("summary", "")).strip()
            if not title:
                continue
            category = classify(title, desc)
            if not category:
                continue
            story = {
                "title": title,
                "source": source,
                "url": entry.get("link", ""),
                "published_date": pub_date.isoformat() if pub_date else None,
                "category": category,
            }
            stories.append(story)
    except Exception as exc:
        log.error("Failed to fetch %s (%s): %s", source, url, exc)
    return stories


def load_existing() -> dict:
    """Load the current JSON file, grouped by category."""
    if not os.path.exists(OUTPUT_FILE):
        return {cat: [] for cat in CATEGORIES}
    try:
        with open(OUTPUT_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return {cat: [] for cat in CATEGORIES}

    grouped = {cat: [] for cat in CATEGORIES}
    stories = data.get("stories", data) if isinstance(data, dict) else data
    if isinstance(stories, list):
        for story in stories:
            cat = story.get("category")
            if cat in grouped:
                grouped[cat].append(story)
    return grouped


def merge(existing: dict, fresh: list) -> dict:
    """
    Merge fresh stories into the existing pool.
    - De-duplicate by URL.
    - Discard stories older than MAX_AGE_DAYS.
    - Replace oldest entries first when over MAX_PER_CATEGORY.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)

    existing_urls = set()
    for stories in existing.values():
        for s in stories:
            if s.get("url"):
                existing_urls.add(s["url"])

    for story in fresh:
        cat = story.get("category")
        if cat not in existing:
            continue
        if story["url"] in existing_urls:
            continue
        existing[cat].append(story)
        existing_urls.add(story["url"])

    for cat in CATEGORIES:
        pool = existing[cat]
        # Drop expired stories
        pool = [
            s for s in pool
            if s.get("published_date") and
               dateparser.parse(s["published_date"]).astimezone(timezone.utc) >= cutoff
        ]
        # Sort newest-first, cap at limit (oldest replaced first)
        pool.sort(key=lambda s: s.get("published_date") or "", reverse=True)
        existing[cat] = pool[:MAX_PER_CATEGORY]

    return existing


def write_output(grouped: dict) -> None:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    flat = []
    for stories in grouped.values():
        flat.extend(stories)
    output = {
        "country": "Pakistan",
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "story_count": len(flat),
        "stories": flat,
    }
    with open(OUTPUT_FILE, "w", encoding="utf-8") as fh:
        json.dump(output, fh, ensure_ascii=False, indent=2)
    log.info("Wrote %d stories to %s", len(flat), OUTPUT_FILE)


def main():
    log.info("Loading existing data ...")
    existing = load_existing()

    log.info("Fetching %d RSS feeds ...", len(FEEDS))
    fresh = []
    for cfg in FEEDS:
        results = fetch_feed(cfg)
        log.info("  %s — %d stories from %s", cfg["source"], len(results), cfg["url"])
        fresh.extend(results)
        time.sleep(0.5)  # polite crawl delay

    log.info("Merging %d fresh stories ...", len(fresh))
    merged = merge(existing, fresh)

    counts = {cat: len(merged[cat]) for cat in CATEGORIES}
    log.info("Category totals: %s", counts)

    write_output(merged)


if __name__ == "__main__":
    main()
