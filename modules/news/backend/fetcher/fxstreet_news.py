"""Fetch news data from FXStreet's internal tRPC API.

Endpoint reverse-engineered from www.fxstreet.com.har:
GET https://www.fxstreet.com/api/v1/trpc/postsListMultifeed.batchList

No authentication is required — only browser-like headers.

Each article page embeds a schema.org NewsArticle JSON-LD block containing
a summary (description) and the full article text (articleBody), which is
scraped per post unless --no-content is passed.

Usage:
    python fxstreet_news.py                # print latest news to stdout
    python fxstreet_news.py --limit 10     # 10 posts per category
    python fxstreet_news.py --no-content   # skip fetching article pages
    python fxstreet_news.py --out news.json
"""

import argparse
import json
import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

import requests

import impact

BASE_URL = "https://www.fxstreet.com/api/v1/trpc/postsListMultifeed.batchList"

HEADERS = {
    "accept": "*/*",
    "content-type": "application/json",
    "referer": "https://www.fxstreet.com/news",
    "user-agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
    ),
}

# Feed / tag GUIDs captured from the HAR file.
FEEDS = {
    "forex_news": "62D504F0-AE31-4D3D-BBA5-11FB479D51AE",
    "crypto_news": "254b9d70-7c4b-481b-a94e-aaac0c60ed14",
    "signatures": [
        "3011f3a6-dfbd-460a-ab54-8bd5921a10a0",
        "81521404-3dfd-4514-970b-8ccac4dd2b22",
        "4609a347-a7d2-4fa5-86a0-fb6a95dceb82",
    ],
}

FOREX_MAJOR_TAGS = [
    "5F91AD8F-26CD-4643-9233-46BD18B03A70",
    "A6744C19-FE88-488F-8044-FB1F574CA818",
    "71F084AA-8636-45A8-B08C-BA41A091BE85",
    "91953627-3E2C-433D-97E3-6A50D83C3F9D",
    "2B905E80-74EB-4224-8163-DE2557A73750",
    "EB3EF1FA-4D91-4251-B962-D628CE19E14E",
    "31CDFBA0-5B00-4E5A-A85C-E083DF2689A1",
]


def build_listings(limit: int) -> list[dict]:
    """One listing per news category, mirroring what the site requests."""
    return [
        {
            "feeds": [FEEDS["forex_news"]],
            "tags": FOREX_MAJOR_TAGS,
            "title": "Forex Majors",
            "limit": limit,
        },
        {
            "feeds": [FEEDS["crypto_news"]],
            "tags": [],
            "title": "Cryptocurrencies",
            "limit": limit,
        },
        {
            "feeds": FEEDS["signatures"],
            "tags": [],
            "title": "Signatures",
            "limit": limit,
        },
    ]


def fetch_news(listings: list[dict], timeout: int = 30) -> dict[str, list[dict]]:
    """Call the tRPC batch endpoint and return {category: [posts]}."""
    payload = {"0": {"json": {"listings": listings}}}
    params = {"batch": "1", "input": json.dumps(payload, separators=(",", ":"))}
    url = f"{BASE_URL}?{urllib.parse.urlencode(params)}"

    resp = requests.get(url, headers=HEADERS, timeout=timeout)
    resp.raise_for_status()

    result = resp.json()[0]["result"]["data"]["json"]
    return {category: block["posts"] for category, block in result.items()}


JSON_LD_RE = re.compile(
    r'<script type="application/ld\+json">(.*?)</script>', re.S
)


def fetch_article_content(url: str, timeout: int = 30) -> dict:
    """Scrape an article page's NewsArticle JSON-LD for summary and body text."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException as exc:
        return {"description": None, "text": None, "contentError": str(exc)}

    for block in JSON_LD_RE.findall(resp.text):
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        if data.get("@type") == "NewsArticle":
            return {
                "description": data.get("description"),
                "text": data.get("articleBody"),
            }
    return {"description": None, "text": None, "contentError": "no NewsArticle JSON-LD found"}


def add_article_content(news: dict[str, list[dict]], max_workers: int = 5) -> None:
    """Fetch each post's page and attach description/text fields in place."""
    posts = [p for category_posts in news.values() for p in category_posts]
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        contents = pool.map(lambda p: fetch_article_content(p["fullUrl"]), posts)
    for post, content in zip(posts, contents):
        post.update(content)


def to_articles(news: dict[str, list[dict]]) -> list[dict]:
    """Flatten categories into one list keeping only the output fields.

    versionDate is the last time the article was edited (it equals
    publicationDate when the article was never updated after publishing).
    """
    return [
        impact.annotate(
            {
                "id": post["id"],
                "date": post.get("versionDate") or post["publicationDate"],
                "title": post["title"].strip(),
                "description": post.get("description"),
                "text": post.get("text"),
                "category": category,
                "source": "fxstreet",
            }
        )
        for category, posts in news.items()
        for post in posts
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch FXStreet news data")
    parser.add_argument("--limit", type=int, default=10, help="posts per category (default: 10)")
    parser.add_argument("--out", help="write results to a JSON file instead of just printing")
    parser.add_argument(
        "--no-content",
        action="store_true",
        help="skip visiting each article page for description/text",
    )
    args = parser.parse_args()

    news = fetch_news(build_listings(args.limit))

    if not args.no_content:
        total = sum(len(p) for p in news.values())
        print(f"Fetching content for {total} articles...")
        add_article_content(news)

    articles = to_articles(news)

    for article in articles:
        print(f"- [{article['impact_level'].upper()}] {article['date'][:16]}  {article['title']}")
        if article["instruments"]:
            print(f"  instruments: {', '.join(article['instruments'])}")

    if args.out:
        with open(args.out, "w") as f:
            json.dump(articles, f, indent=2)
        print(f"\nSaved {len(articles)} articles to {args.out}")


if __name__ == "__main__":
    main()
