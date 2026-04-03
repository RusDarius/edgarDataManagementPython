"""Sherwood News (sherwood.news/markets/) feed gatherer.

Scrapes the markets section of Sherwood News via its public sitemap and
individual article pages.  Stores structured feed data in a JSON file under
``logs/sherwood_news/`` with URL-based deduplication so repeated runs only
append genuinely new articles.

The stored format is intentionally flat and AI-friendly — each article carries
its own metadata so a downstream model can consume the file directly for
summarisation, trend extraction, or ticker-level news mapping.

Usage (from project root or as an import)::

    from src.data_analysis_scripts.sherwood_news_feed_gather import (
        gather_sherwood_markets_feed,
        fetch_and_log_full_articles,
    )

    # Gather one batch (equivalent to "one scroll") of the latest articles.
    result = gather_sherwood_markets_feed()
    print(result["new_articles"], "new articles stored")
    print(result["feed_file"])

    # Gather with full article body text (slower, one HTTP request per article).
    result = gather_sherwood_markets_feed(fetch_full_content=True)

    # Fetch full content for articles missing body text and write .log dump.
    result = fetch_and_log_full_articles(max_articles=10)
    print(result["articles_fetched"], "articles newly fetched")
    print(result["session_log"])
"""

from __future__ import annotations

import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup

SRC_DIR = Path(__file__).resolve().parents[1]
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from generic_utils.log_to_files_util import log_to_file

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SITEMAP_URL = "https://sherwood.news/sitemap.xml"
BASE_URL = "https://sherwood.news"
MARKETS_PATH_PREFIX = "/markets/"

LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs" / "sherwood_news"
FEED_FILE = LOG_DIR / "sherwood_markets_feed.json"
SESSION_LOG_DIR = LOG_DIR / "sessions"

SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
NEWS_NS = {"news": "http://www.google.com/schemas/sitemap-news/0.9"}

REQUEST_TIMEOUT = 20
# Polite delay between individual article fetches (seconds).
ARTICLE_FETCH_DELAY = 0.6

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# ---------------------------------------------------------------------------
# Feed storage helpers
# ---------------------------------------------------------------------------


def _load_existing_feed() -> list[dict[str, Any]]:
    """Load previously stored articles, returning an empty list when none exist."""
    if not FEED_FILE.exists():
        return []
    try:
        data = json.loads(FEED_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return []


def _save_feed(articles: list[dict[str, Any]]) -> None:
    FEED_FILE.parent.mkdir(parents=True, exist_ok=True)
    FEED_FILE.write_text(
        json.dumps(articles, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )


def _existing_urls(articles: list[dict[str, Any]]) -> set[str]:
    return {a["url"] for a in articles if "url" in a}


def _deduplicate_feed(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return articles deduplicated by URL, keeping the first occurrence."""
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for a in articles:
        url = a.get("url", "")
        if url and url in seen:
            continue
        seen.add(url)
        result.append(a)
    return result


def _daily_log_path(prefix: str, ext: str = ".log") -> Path:
    """Return a daily log file path like ``sessions/prefix_YYYYMMDD.log``.

    All invocations on the same UTC day share the same file (appended to).
    """
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    SESSION_LOG_DIR.mkdir(parents=True, exist_ok=True)
    return SESSION_LOG_DIR / f"{prefix}_{day}{ext}"


# ---------------------------------------------------------------------------
# Sitemap parsing
# ---------------------------------------------------------------------------


def _fetch_sitemap() -> str:
    response = requests.get(
        SITEMAP_URL,
        headers={"User-Agent": _USER_AGENT},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return response.text


def _parse_sitemap_entries(xml_text: str) -> list[dict[str, Any]]:
    """Extract markets-category articles from the sitemap XML."""
    root = ET.fromstring(xml_text)
    entries: list[dict[str, Any]] = []

    for url_el in root.findall("sm:url", SITEMAP_NS):
        loc_el = url_el.find("sm:loc", SITEMAP_NS)
        if loc_el is None or loc_el.text is None:
            continue

        loc = loc_el.text.strip()

        # Only keep /markets/ articles (skip the bare /markets/ index page).
        path = loc.replace(BASE_URL, "")
        if not path.startswith(MARKETS_PATH_PREFIX):
            continue
        slug = path[len(MARKETS_PATH_PREFIX) :].strip("/")
        if not slug:
            continue

        lastmod_el = url_el.find("sm:lastmod", SITEMAP_NS)
        lastmod = (
            lastmod_el.text.strip()
            if lastmod_el is not None and lastmod_el.text
            else None
        )

        # Extract <news:news> block when available.
        news_el = url_el.find(".//news:news", NEWS_NS)
        title: str | None = None
        pub_date: str | None = None
        if news_el is not None:
            title_el = news_el.find("news:title", NEWS_NS)
            title = (
                title_el.text.strip()
                if title_el is not None and title_el.text
                else None
            )
            pub_date_el = news_el.find("news:publication_date", NEWS_NS)
            pub_date = (
                pub_date_el.text.strip()
                if pub_date_el is not None and pub_date_el.text
                else None
            )

        entries.append(
            {
                "url": loc,
                "slug": slug,
                "title": title,
                "published_at": pub_date,
                "last_modified": lastmod,
            }
        )

    return entries


# ---------------------------------------------------------------------------
# Individual article scraping
# ---------------------------------------------------------------------------

_TICKER_PATTERN = re.compile(r"([A-Z]{1,5})\s+\$[\d,]+(?:\.\d+)?\s*\([+-]?[\d.]+%\)")


def _fetch_article_content(url: str) -> dict[str, Any]:
    """Fetch a single article page and extract structured content."""
    response = requests.get(
        url,
        headers={"User-Agent": _USER_AGENT},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "lxml")

    # -- title -----------------------------------------------------------------
    title = None
    og_title = soup.find("meta", property="og:title")
    if og_title and og_title.get("content"):
        title = og_title["content"]
    if not title:
        h1 = soup.find("h1")
        if h1:
            title = h1.get_text(strip=True)

    # -- description / excerpt -------------------------------------------------
    description = None
    og_desc = soup.find("meta", property="og:description")
    if og_desc and og_desc.get("content"):
        description = og_desc["content"]

    # -- author ----------------------------------------------------------------
    author = None
    author_tag = soup.find("meta", attrs={"name": "author"})
    if author_tag and author_tag.get("content"):
        author = author_tag["content"]
    if not author:
        # fallback: look for author link patterns in the page
        author_link = soup.find("a", href=re.compile(r"/author/"))
        if author_link:
            author = author_link.get_text(strip=True)

    # -- published date --------------------------------------------------------
    published_at = None
    time_tag = soup.find("time")
    if time_tag and time_tag.get("datetime"):
        published_at = time_tag["datetime"]
    if not published_at:
        pub_meta = soup.find("meta", property="article:published_time")
        if pub_meta and pub_meta.get("content"):
            published_at = pub_meta["content"]

    # -- body text -------------------------------------------------------------
    body_text = ""
    article_tag = soup.find("article")
    if article_tag is None:
        # Sherwood News data/analysis articles skip the <article> element and
        # put their content directly in <div class="primary-article-content">.
        article_tag = soup.find("div", class_="primary-article-content")
    if article_tag:
        # Remove script / style noise.
        for tag in article_tag.find_all(["script", "style", "noscript"]):
            tag.decompose()
        body_text = article_tag.get_text(separator="\n", strip=True)
    elif soup.find("main"):
        main = soup.find("main")
        for tag in main.find_all(["script", "style", "noscript"]):
            tag.decompose()
        body_text = main.get_text(separator="\n", strip=True)

    # -- tickers mentioned -----------------------------------------------------
    full_text = soup.get_text()
    tickers = sorted(set(_TICKER_PATTERN.findall(full_text)))

    return {
        "title": title,
        "description": description,
        "author": author,
        "published_at": published_at,
        "body_text": body_text,
        "tickers_mentioned": tickers,
    }


# ---------------------------------------------------------------------------
# Public gathering function
# ---------------------------------------------------------------------------


def gather_sherwood_markets_feed(
    *,
    fetch_full_content: bool = False,
    max_articles: int | None = None,
) -> dict[str, Any]:
    """Scrape the Sherwood News /markets/ feed and persist new articles.

    Args:
        fetch_full_content: When ``True`` each new article page is fetched
            individually to capture full body text, author, and ticker
            mentions.  This is slower but produces richer data for AI
            analysis.  Defaults to ``False`` (sitemap metadata only).
        max_articles: Cap the number of *new* articles to gather per
            session.  ``None`` means no cap.

    Returns:
        A summary dict with keys ``new_articles``, ``total_articles``,
        ``feed_file``, ``session_log``, and ``articles_added`` (list of
        URLs added this session).
    """
    session_ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    session_log = _daily_log_path("session")

    log_to_file(session_log, "")
    log_to_file(session_log, f"Sherwood News feed gather session | {session_ts}")
    log_to_file(session_log, "=" * 100)
    log_to_file(
        session_log,
        f"fetch_full_content={fetch_full_content} | max_articles={max_articles}",
    )
    log_to_file(session_log, "")

    # 1. Load existing feed for deduplication.
    existing = _load_existing_feed()
    known_urls = _existing_urls(existing)
    log_to_file(session_log, f"Existing feed size: {len(existing)} articles")

    # 2. Fetch and parse sitemap.
    log_to_file(session_log, f"Fetching sitemap: {SITEMAP_URL}")
    try:
        sitemap_xml = _fetch_sitemap()
    except requests.RequestException as exc:
        log_to_file(session_log, f"ERROR fetching sitemap: {exc}")
        return {
            "new_articles": 0,
            "total_articles": len(existing),
            "feed_file": str(FEED_FILE),
            "session_log": str(session_log),
            "articles_added": [],
            "error": str(exc),
        }

    sitemap_entries = _parse_sitemap_entries(sitemap_xml)
    log_to_file(
        session_log,
        f"Sitemap returned {len(sitemap_entries)} /markets/ article entries",
    )

    # 3. Filter to only new articles.
    new_entries = [e for e in sitemap_entries if e["url"] not in known_urls]
    if max_articles is not None:
        new_entries = new_entries[:max_articles]
    log_to_file(session_log, f"New (not yet stored) entries: {len(new_entries)}")
    log_to_file(session_log, "")

    # 4. Build article records.
    added_urls: list[str] = []
    for idx, entry in enumerate(new_entries, 1):
        article: dict[str, Any] = {
            "url": entry["url"],
            "slug": entry["slug"],
            "title": entry.get("title"),
            "published_at": entry.get("published_at"),
            "last_modified": entry.get("last_modified"),
            "category": "markets",
            "source": "sherwood.news",
            "gathered_at": datetime.now(timezone.utc).isoformat(),
        }

        if fetch_full_content:
            log_to_file(
                session_log,
                f"  [{idx}/{len(new_entries)}] Fetching {entry['url']}",
            )
            try:
                content = _fetch_article_content(entry["url"])
                article["title"] = content["title"] or article["title"]
                article["description"] = content.get("description")
                article["author"] = content.get("author")
                article["body_text"] = content.get("body_text", "")
                article["tickers_mentioned"] = content.get("tickers_mentioned", [])
                if content.get("published_at"):
                    article["published_at"] = content["published_at"]
            except requests.RequestException as exc:
                log_to_file(session_log, f"    WARN: could not fetch article: {exc}")
                article["fetch_error"] = str(exc)

            if idx < len(new_entries):
                time.sleep(ARTICLE_FETCH_DELAY)
        else:
            log_to_file(
                session_log,
                f"  [{idx}/{len(new_entries)}] {entry.get('title') or entry['slug']}",
            )

        existing.append(article)
        added_urls.append(entry["url"])

    # 5. Deduplicate, sort by published_at descending, and persist.
    existing = _deduplicate_feed(existing)
    existing.sort(
        key=lambda a: a.get("published_at") or "1970-01-01",
        reverse=True,
    )
    _save_feed(existing)

    log_to_file(session_log, "")
    log_to_file(session_log, f"Articles added this session: {len(added_urls)}")
    log_to_file(session_log, f"Total articles in feed: {len(existing)}")
    log_to_file(session_log, f"Feed file: {FEED_FILE}")

    return {
        "new_articles": len(added_urls),
        "total_articles": len(existing),
        "feed_file": str(FEED_FILE),
        "session_log": str(session_log),
        "articles_added": added_urls,
    }


# ---------------------------------------------------------------------------
# Convenience: read stored feed for AI consumption
# ---------------------------------------------------------------------------


def load_sherwood_feed() -> list[dict[str, Any]]:
    """Return the full stored feed as a list of article dicts."""
    return _load_existing_feed()


def load_sherwood_feed_as_text(
    *,
    max_articles: int | None = None,
    include_body: bool = True,
) -> str:
    """Return a plain-text representation of the feed suitable for
    feeding into an LLM prompt.  Also writes a session ``.txt`` file
    under ``logs/sherwood_news/sessions/`` with the article count and
    the full text of every article for downstream processing.

    Duplicate articles (by URL) are automatically excluded so each
    article appears only once in both the returned string and the
    session file.

    Args:
        max_articles: Limit the number of articles included.
        include_body: Include the full body text when available.

    Returns:
        A formatted text block with one section per article.
    """
    articles = _load_existing_feed()
    unique_articles = _deduplicate_feed(articles)

    if max_articles is not None:
        unique_articles = unique_articles[:max_articles]

    parts: list[str] = []
    for article in unique_articles:
        header = (
            f"Title: {article.get('title', 'N/A')}\n"
            f"URL: {article.get('url', '')}\n"
            f"Published: {article.get('published_at', 'N/A')}\n"
            f"Author: {article.get('author', 'N/A')}\n"
        )
        tickers = article.get("tickers_mentioned")
        if tickers:
            header += f"Tickers: {', '.join(tickers)}\n"
        description = article.get("description", "")
        body = article.get("body_text", "")

        content = ""
        if include_body and body:
            content = body
        elif description:
            content = description

        parts.append(f"{header}\n{content}\n")

    separator = "\n" + "=" * 80 + "\n"
    full_text = separator.join(parts)

    # -- Write daily session .txt file with article count + full text -----
    session_txt = _daily_log_path("feed_text", ".txt")

    generated_ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    header_block = (
        f"Sherwood News Feed — {len(unique_articles)} articles\n"
        f"Generated: {generated_ts}\n"
        f"{'=' * 80}\n\n"
    )
    session_txt.write_text(header_block + full_text, encoding="utf-8")

    return full_text


def fetch_and_log_full_articles(
    *,
    max_articles: int | None = None,
) -> dict[str, Any]:
    """Fetch full content for feed articles that haven't been scraped yet,
    update the persisted feed, and write a ``.log`` session file containing
    ALL article data (metadata + full body text).

    Articles are deduplicated by URL.  Articles whose ``body_text`` is
    already populated are *not* re-fetched, so this is safe to call
    repeatedly — only articles still missing content will be scraped.

    Args:
        max_articles: Cap the number of articles to process (after
            deduplication).  ``None`` means process the entire feed.

    Returns:
        A summary dict with keys ``total_articles``,
        ``articles_fetched`` (count of newly scraped articles this call),
        ``session_log``, and ``feed_file``.
    """
    session_ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    session_log = _daily_log_path("full_article_scan")

    log_to_file(session_log, "")
    log_to_file(session_log, f"Full article content scan | {session_ts}")
    log_to_file(session_log, "=" * 100)

    # 1. Load & deduplicate by URL.
    all_articles = _load_existing_feed()
    unique_articles = _deduplicate_feed(all_articles)

    log_to_file(
        session_log,
        f"Feed loaded: {len(all_articles)} entries, "
        f"{len(unique_articles)} unique articles",
    )

    # Determine which articles to process (fetch + log).  The cap only
    # limits processing — the full deduplicated feed is always persisted.
    articles_to_process = unique_articles
    if max_articles is not None:
        articles_to_process = unique_articles[:max_articles]
        log_to_file(session_log, f"Capped processing to max_articles={max_articles}")

    # 2. Fetch full content for articles still missing body_text.
    fetched_count = 0
    fetched_articles: list[dict[str, Any]] = []
    for idx, article in enumerate(articles_to_process, 1):
        already_has_body = bool(article.get("body_text", "").strip())
        label = article.get("title") or article.get("slug") or article.get("url", "")

        if already_has_body:
            log_to_file(
                session_log,
                f"  [{idx}/{len(articles_to_process)}] SKIP (already scanned): {label}",
            )
            continue

        url = article.get("url", "")
        if not url:
            log_to_file(
                session_log,
                f"  [{idx}/{len(articles_to_process)}] SKIP (no URL): {label}",
            )
            continue

        log_to_file(
            session_log,
            f"  [{idx}/{len(articles_to_process)}] Fetching: {url}",
        )
        try:
            content = _fetch_article_content(url)
            article["title"] = content["title"] or article.get("title")
            article["description"] = content.get("description")
            article["author"] = content.get("author")
            article["body_text"] = content.get("body_text", "")
            article["tickers_mentioned"] = content.get("tickers_mentioned", [])
            if content.get("published_at"):
                article["published_at"] = content["published_at"]
            fetched_count += 1
            fetched_articles.append(article)
        except requests.RequestException as exc:
            log_to_file(session_log, f"    WARN: fetch failed: {exc}")
            article.setdefault("fetch_error", str(exc))

        if idx < len(articles_to_process):
            time.sleep(ARTICLE_FETCH_DELAY)

    log_to_file(session_log, "")
    log_to_file(session_log, f"Newly fetched this session: {fetched_count}")

    # 3. Persist the full deduplicated (and now enriched) feed.
    unique_articles.sort(
        key=lambda a: a.get("published_at") or "1970-01-01",
        reverse=True,
    )
    _save_feed(unique_articles)
    log_to_file(session_log, f"Feed saved: {len(unique_articles)} articles")

    # 4. Write only the newly fetched articles into the session log.
    log_to_file(session_log, "")
    log_to_file(session_log, "=" * 100)
    log_to_file(
        session_log,
        f"NEWLY FETCHED ARTICLES — {len(fetched_articles)} articles",
    )
    log_to_file(session_log, "=" * 100)

    for article in fetched_articles:
        log_to_file(session_log, "")
        log_to_file(session_log, "-" * 80)
        log_to_file(session_log, f"Title:     {article.get('title', 'N/A')}")
        log_to_file(session_log, f"URL:       {article.get('url', '')}")
        log_to_file(session_log, f"Published: {article.get('published_at', 'N/A')}")
        log_to_file(session_log, f"Author:    {article.get('author', 'N/A')}")
        tickers = article.get("tickers_mentioned")
        if tickers:
            log_to_file(session_log, f"Tickers:   {', '.join(tickers)}")
        desc = article.get("description", "")
        if desc:
            log_to_file(session_log, f"Excerpt:   {desc}")
        body = article.get("body_text", "")
        if body:
            log_to_file(session_log, "")
            log_to_file(session_log, body)
        log_to_file(session_log, "-" * 80)

    log_to_file(session_log, "")
    log_to_file(session_log, f"Session log: {session_log}")
    log_to_file(session_log, f"Feed file:   {FEED_FILE}")

    return {
        "total_articles": len(unique_articles),
        "articles_processed": len(articles_to_process),
        "articles_fetched": fetched_count,
        "session_log": str(session_log),
        "feed_file": str(FEED_FILE),
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Gather Sherwood News /markets/ feed articles."
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Fetch full article content (slower, richer data).",
    )
    parser.add_argument(
        "--max",
        type=int,
        default=None,
        help="Maximum number of new articles to gather.",
    )
    args = parser.parse_args()

    result = gather_sherwood_markets_feed(
        fetch_full_content=args.full,
        max_articles=args.max,
    )

    print(f"New articles gathered: {result['new_articles']}")
    print(f"Total articles stored: {result['total_articles']}")
    print(f"Feed file: {result['feed_file']}")
    print(f"Session log: {result['session_log']}")
    if result.get("error"):
        print(f"Error: {result['error']}")
