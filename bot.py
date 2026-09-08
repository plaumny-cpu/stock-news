#!/usr/bin/env python3
"""Stock news → Discord (RSS + Finnhub, ฟรี 100%)"""

import os, re, json, time, html, hashlib
from datetime import datetime, timezone
from pathlib import Path

import requests
import feedparser

# ─────────── CONFIG ───────────
WEBHOOK      = os.environ["DISCORD_WEBHOOK"]
FINNHUB_KEY  = os.environ.get("FINNHUB_KEY", "")
STATE_FILE   = Path("seen.json")
MAX_PER_RUN  = 12          # กันสแปมตอนรันครั้งแรก
KEEP_STATE   = 3000        # เก็บ id ข่าวเก่ากี่รายการ
UA           = "StockNewsBot/1.0 (your-email@example.com)"   # ← แก้เป็นอีเมลคุณ

# ใส่ ticker ที่สนใจ; ปล่อยว่าง [] = รับทุกข่าว
WATCH_TICKERS = []
# ใส่คีย์เวิร์ด; ปล่อยว่าง [] = ไม่กรอง
KEYWORDS = []
# ตัวอย่าง: KEYWORDS = ["earnings", "merger", "acquisition", "FDA", "guidance", "lawsuit"]

FEEDS = {
    "Yahoo Finance": "https://finance.yahoo.com/news/rssindex",
    "Nasdaq":        "https://www.nasdaq.com/feed/rssoutbound?category=Stocks",
    "CNBC Markets":  "https://www.cnbc.com/id/20910258/device/rss/rss.html",
    "MarketWatch":   "https://feeds.content.dowjones.io/public/rss/mw_topstories",
    "Investing.com": "https://www.investing.com/rss/news_25.rss",
    "SEC 8-K":       "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K&output=atom",
}

COLORS = {
    "Yahoo Finance": 0x7d3cff, "Nasdaq": 0x0092d0, "CNBC Markets": 0x005594,
    "MarketWatch": 0x00a651, "Investing.com": 0xff6600, "SEC 8-K": 0xc0392b,
    "Finnhub": 0x2ecc71,
}

# ─────────── STATE ───────────
def load_seen():
    if STATE_FILE.exists():
        try:
            return set(json.loads(STATE_FILE.read_text()))
        except Exception:
            return set()
    return set()

def save_seen(seen):
    STATE_FILE.write_text(json.dumps(list(seen)[-KEEP_STATE:], indent=0))

# ─────────── HELPERS ───────────
def clean(text, limit=350):
    text = re.sub(r"<[^>]+>", "", text or "")
    text = html.unescape(text).strip()
    return (text[:limit] + "…") if len(text) > limit else text

def uid(link):
    return hashlib.md5(link.encode()).hexdigest()[:16]

def passes_filter(title, summary):
    blob = f"{title} {summary}"
    if KEYWORDS and not any(k.lower() in blob.lower() for k in KEYWORDS):
        return False
    if WATCH_TICKERS:
        found = set(re.findall(r"\b[A-Z]{1,5}\b", blob))
        if not (found & set(WATCH_TICKERS)):
            return False
    return True

def post(source, title, link, summary, ts=None):
    embed = {
        "title": clean(title, 250) or "(no title)",
        "url": link,
        "description": clean(summary),
        "color": COLORS.get(source, 0x95a5a6),
        "footer": {"text": f"📰 {source}"},
        "timestamp": (ts or datetime.now(timezone.utc)).isoformat(),
    }
    for attempt in range(3):
        r = requests.post(WEBHOOK, json={"embeds": [embed]}, timeout=15)
        if r.status_code == 429:                       # rate limited
            time.sleep(float(r.json().get("retry_after", 2)))
            continue
        if r.status_code in (200, 204):
            return True
        print(f"  ✗ Discord {r.status_code}: {r.text[:120]}")
        return False
    return False

# ─────────── SOURCES ───────────
def from_rss():
    items = []
    for source, url in FEEDS.items():
        try:
            raw = requests.get(url, headers={"User-Agent": UA}, timeout=20).content
            feed = feedparser.parse(raw)
            for e in feed.entries[:20]:
                link = e.get("link")
                if not link:
                    continue
                items.append({
                    "source": source,
                    "title": e.get("title", ""),
                    "link": link,
                    "summary": e.get("summary", ""),
                })
            print(f"  ✓ {source}: {len(feed.entries)} entries")
        except Exception as ex:
            print(f"  ✗ {source}: {ex}")
    return items

def from_finnhub():
    if not FINNHUB_KEY:
        return []
    try:
        r = requests.get("https://finnhub.io/api/v1/news",
                         params={"category": "general", "token": FINNHUB_KEY},
                         timeout=20)
        data = r.json()
        print(f"  ✓ Finnhub: {len(data)} entries")
        return [{
            "source": "Finnhub",
            "title": n.get("headline", ""),
            "link": n.get("url", ""),
            "summary": n.get("summary", ""),
        } for n in data[:40] if n.get("url")]
    except Exception as ex:
        print(f"  ✗ Finnhub: {ex}")
        return []

# ─────────── MAIN ───────────
def main():
    seen = load_seen()
    first_run = len(seen) == 0
    print(f"State: {len(seen)} seen items")

    items = from_rss() + from_finnhub()

    fresh = []
    for it in items:
        key = uid(it["link"])
        if key in seen:
            continue
        seen.add(key)                      # mark ทันที กันซ้ำในรอบเดียวกัน
        if passes_filter(it["title"], it["summary"]):
            fresh.append(it)

    if first_run:
        print("First run — seeding state, sending only 3 samples")
        fresh = fresh[:3]

    sent = 0
    for it in fresh[:MAX_PER_RUN]:
        if post(it["source"], it["title"], it["link"], it["summary"]):
            sent += 1
        time.sleep(1.2)                    # กัน Discord rate limit

    save_seen(seen)
    print(f"Done: {sent} sent / {len(fresh)} new / {len(items)} fetched")

if __name__ == "__main__":
    main()
