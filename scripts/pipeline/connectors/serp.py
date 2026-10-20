"""Состав выдачи через Brave: кто занимает топ — бренды, форумы, видео, документация.

Это проверка проходимости для продукта без аутрича: если топ-5 — брендовые SaaS с высоким
авторитетом, новой странице там не появиться; если форумы, видео и блоги — есть щель.
"""
from __future__ import annotations

import urllib.parse

from scripts import search

FORUM = ("reddit.com", "stackoverflow.com", "stackexchange.com", "news.ycombinator.com", "quora.com",
         "groups.google.com", "discourse.", "community.", "forum.", "forums.", "discuss.")
VIDEO = ("youtube.com", "youtu.be", "vimeo.com")
DOCS = ("docs.", "developer.", "developers.", "learn.microsoft.com", "cloud.google.com", "aws.amazon.com",
        "readthedocs.io", "registry.terraform.io", "kubernetes.io", "argoproj.github.io", "help.", "support.")
BLOG_HOSTS = ("medium.com", "dev.to", "substack.com", "hashnode.dev", "wordpress.com", "blogspot.com", "linkedin.com")
BLOG_PATHS = ("/blog", "/articles", "/article/", "/guide", "/learn", "/resources", "/academy", "/insights",
              "/news", "/post", "/tutorial", "/how-to", "/what-is")
MARKETPLACE = ("apps.shopify.com", "marketplace.", "chromewebstore.google.com", "wordpress.org/plugins",
               "apify.com/store", "producthunt.com", "npmjs.com", "pypi.org", "github.com")
REVIEWS = ("g2.com", "capterra.com", "alternativeto.net", "trustradius.com", "getapp.com", "softwareadvice.com")
REFERENCE = ("wikipedia.org",)


def classify(url):
    """Тип результата: forum | video | docs | marketplace | reviews | reference | blog | brand."""
    parsed = urllib.parse.urlparse(url or "")
    host = parsed.netloc.lower().split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    path = parsed.path.lower()
    full = host + path
    if any(host == h or host.endswith("." + h) for h in VIDEO):
        return "video"
    if any(host == h or host.endswith("." + h) or host.startswith(h) for h in FORUM):
        return "forum"
    if any(h in full for h in REVIEWS):
        return "reviews"
    if any(h in full for h in REFERENCE):
        return "reference"
    if any(h in full for h in MARKETPLACE):
        return "marketplace"
    if any(host.startswith(h) or ("." + h) in host or h in host for h in DOCS):
        return "docs"
    if any(host == h or host.endswith("." + h) for h in BLOG_HOSTS):
        return "blog"
    if any(marker in path for marker in BLOG_PATHS):
        return "blog"
    depth = len([p for p in path.split("/") if p])
    if depth <= 2:
        return "brand"
    return "blog"


def top(run_id, phrase, *, count=10, country="US", language="en", provider="brave", root=None, who=None):
    result = search.search(run_id, phrase, provider=provider, count=count, country=country, search_lang=language,
                           root=root or search.ROOT, **(who or {}))
    rows = []
    for item in result.get("results") or []:
        host = urllib.parse.urlparse(item["url"]).netloc.lower().removeprefix("www.")
        rows.append({"rank": item["rank"], "url": item["url"], "domain": host, "title": item.get("title"),
                     "kind": classify(item["url"])})
    top5 = [r for r in rows if r["rank"] <= 5]
    return {"phrase": phrase, "source_id": result.get("source_id"), "status": result.get("retrieval_status"),
            "error": result.get("error"), "results": rows,
            "brands_top5": sum(r["kind"] == "brand" for r in top5),
            "forums_top10": sum(r["kind"] in ("forum", "video", "reference") for r in rows),
            "kinds_top5": [r["kind"] for r in top5]}
