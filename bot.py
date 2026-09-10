#!/usr/bin/env python3
"""Stock news → Discord (RSS + Finnhub) — พร้อมสรุปย่อ รูปภาพ และแปลไทย"""

import os, re, json, time, html, hashlib, urllib.parse
from datetime import datetime, timezone
from pathlib import Path

import requests
import feedparser

# ══════════════ CONFIG ══════════════
WEBHOOK     = os.environ["DISCORD_WEBHOOK"]
FINNHUB_KEY = os.environ.get("FINNHUB_KEY", "")
STATE_FILE  = Path("seen.json")

MAX_PER_RUN  = 12
FIRST_RUN_N  = 3
KEEP_STATE   = 3000
UA           = "StockNewsBot/1.0 (your-email@example.com)"   # ← แก้เป็นอีเมลคุณ

TRANSLATE_TH = True     # True = แปลไทย, False = อังกฤษล้วน
SHOW_IMAGE   = True     # True = แนบรูป
BIG_IMAGE    = True     # True = รูปใหญ่, False = รูปเล็กมุมขวา
SUMMARY_LEN  = 300

WATCH_TICKERS = []      # เช่น ["NVDA", "TSLA"] ; [] = รับทุกข่าว
KEYWORDS      = []      # เช่น ["earnings", "merger"] ; [] = ไม่กรอง

FEEDS = {
    "Yahoo Finance": "https://finance.yahoo.com/news/rssindex",
    "Nasdaq":        "https://www.nasdaq.com/feed/rssoutbound?category=Stocks",
    "CNBC Markets":  "https://www.cnbc.com/id/20910258/device/rss/rss.html",
    "MarketWatch":   "https://feeds.content.dowjones.io/public/rss/mw_topstories",
    "Investing.com": "https://www.investing.com/rss/news_25.rss",
    "SEC 8-K":       "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K&output=atom",
}

COLORS = {
    "Yahoo Finance": 0x7B2FF7, "Nasdaq": 0x0092CF, "CNBC Markets": 0x005594,
    "MarketWatch": 0x00A94F, "Investing.com": 0xE8A33D,
    "SEC 8-K": 0xC0392B, "Finnhub": 0x1DB954,
}

ICONS = {
    "Yahoo Finance": "🟣", "Nasdaq": "🔵", "CNBC Markets": "🔷",
    "MarketWatch": "🟢", "Investing.com": "🟠",
    "SEC 8-K": "🔴", "Finnhub": "💚",
}


# ══════════════ STATE ══════════════
def load_seen() -> set:
    if STATE_FILE.exists():
        try:
            return set(json.loads(STATE_FILE.read_text()))
        except Exception:
            pass
    return set()


def save_seen(seen: set):
    STATE_FILE.write_text(json.dumps(list(seen)[-KEEP_STATE:], indent=0))


# ══════════════ HELPERS ══════════════
def clean_html(raw: str, limit: int = SUMMARY_LEN) -> str:
    if not raw:
        return ""
    txt = re.sub(r"<[^>]+>", " ", raw)
    txt = html.unescape(txt)
    txt = re.sub(r"\s+", " ", txt).strip()
    if len(txt) > limit:
        txt = txt[:limit].rsplit(" ", 1)[0] + "…"
    return txt


def extract_image(entry) -> str | None:
    for key in ("media_thumbnail", "media_content"):
        media = entry.get(key)
        if media and isinstance(media, list):
            url = media[0].get("url")
            if url:
                return url
    for enc in entry.get("enclosures") or []:
        if "image" in (enc.get("type") or ""):
            return enc.get("href") or enc.get("url")
    blob = entry.get("summary", "") or entry.get("description", "")
    m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', blob)
    return m.group(1) if m else None


def extract_summary(entry) -> str:
    raw = entry.get("summary") or entry.get("description") or ""
    if not raw and entry.get("content"):
        raw = entry["content"][0].get("value", "")
    return clean_html(raw)


def extract_time(entry) -> str | None:
    for key in ("published_parsed", "updated_parsed"):
        tm = entry.get(key)
        if tm:
            try:
                return datetime(*tm[:6], tzinfo=timezone.utc).isoformat()
            except Exception:
                pass
    return None


def to_thai(text: str) -> str:
    if not TRANSLATE_TH or not text or not text.strip():
        return text
    try:
        url = ("https://translate.googleapis.com/translate_a/single"
               "?client=gtx&sl=en&tl=th&dt=t&q=" + urllib.parse.quote(text[:1500]))
        r = requests.get(url, timeout=8)
        if r.status_code == 200:
            return "".join(seg[0] for seg in r.json()[0] if seg[0])
    except Exception:
        pass
    return text


def uid(link: str) -> str:
    return hashlib.md5(link.encode()).hexdigest()[:16]


def passes_filter(title: str, summary: str) -> bool:
    blob = f"{title} {summary}"
    if KEYWORDS and not any(k.lower() in blob.lower() for k in KEYWORDS):
        return False
    if WATCH_TICKERS:
        found = set(re.findall(r"\b[A-Z]{1,5}\b", blob))
        if not (found & set(WATCH_TICKERS)):
            return False
    return True


# ══════════════ EMBED ══════════════
def build_embed(item: dict) -> dict:
    src   = item["source"]
    icon  = ICONS.get(src, "📰")
    title = item["title"]
    body  = item.get("summary") or ""

    if TRANSLATE_TH:
        th_title = to_thai(title)
        th_body  = to_thai(body) if body else ""
        desc = th_body or "_(แหล่งข่าวไม่ได้ให้คำอธิบาย)_"
        if th_title.strip() != title.strip():
            desc += f"\n\n> 🇬🇧 *{title[:200]}*"
        show_title = th_title
    else:
        desc = body or "_(no description provided)_"
        show_title = title

    embed = {
        "title":       show_title[:250],
        "url":         item["link"],
        "description": desc[:1000],
        "color":       COLORS.get(src, 0x5865F2),
        "footer":      {"text": f"{icon} {src} · กดหัวข้อเพื่ออ่านข่าวเต็ม"},
    }
    if item.get("published_iso"):
        embed["timestamp"] = item["published_iso"]
    if SHOW_IMAGE and item.get("image"):
        key = "image" if BIG_IMAGE else "thumbnail"
        embed[key] = {"url": item["image"]}
    return embed


def send_batch(items: list) -> int:
    sent = 0
    for i in range(0, len(items), 10):
        chunk   = items[i:i + 10]
        payload = {"username": "Stock News", "embeds": [build_embed(it) for it in chunk]}
        for _ in range(3):
            try:
                r = requests.post(WEBHOOK, json=payload, timeout=20)
            except Exception as ex:
                print(f"  ✗ Discord error: {ex}")
                break
            if r.status_code == 429:
                wait = float(r.json().get("retry_after", 2))
                print(f"  … rate limited, waiting {wait}s")
                time.sleep(wait)
                continue
            if r.status_code in (200, 204):
                sent += len(chunk)
            else:
                print(f"  ✗ Discord {r.status_code}: {r.text[:200]}")
            break
        time.sleep(1.2)
    return sent


# ══════════════ SOURCES ══════════════
def from_rss() -> list:
    items = []
    for source, url in FEEDS.items():
        try:
            raw  = requests.get(url, headers={"User-Agent": UA}, timeout=20).content
            feed = feedparser.parse(raw)
            for e in feed.entries[:20]:
                link = e.get("link")
                if not link:
                    continue
                items.append({
                    "source":        source,
                    "title":         e.get("title", "").strip(),
                    "link":          link,
                    "summary":       extract_summary(e),
                    "image":         extract_image(e),
                    "published_iso": extract_time(e),
                })
            print(f"  ✓ {source}: {len(feed.entries)} entries")
        except Exception as ex:
            print(f"  ✗ {source}: {ex}")
    return items


def from_finnhub() -> list:
    if not FINNHUB_KEY:
        print("  – Finnhub: no API key, skipped")
        return []
    try:
        r = requests.get("https://finnhub.io/api/v1/news",
                         params={"category": "general", "token": FINNHUB_KEY},
                         timeout=20)
        data = r.json()
        print(f"  ✓ Finnhub: {len(data)} entries")
        out = []
        for n in data[:40]:
            if not n.get("url"):
                continue
            ts = n.get("datetime")
            out.append({
                "source":        "Finnhub",
                "title":         (n.get("headline") or "").strip(),
                "link":          n["url"],
                "summary":       clean_html(n.get("summary", "")),
                "image":         n.get("image") or None,
                "published_iso": datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts else None,
            })
        return out
    except Exception as ex:
        print(f"  ✗ Finnhub: {ex}")
        return []


# ══════════════ MAIN ══════════════
def main():
    seen      = load_seen()
    first_run = len(seen) == 0
    print(f"State: {len(seen)} seen items")

    items = from_rss() + from_finnhub()

    fresh = []
    for it in items:
        if not it["title"]:
            continue
        key = uid(it["link"])
        if key in seen:
            continue
        seen.add(key)
        if passes_filter(it["title"], it["summary"]):
            fresh.append(it)

    if first_run:
        print(f"First run — seeding state, sending only {FIRST_RUN_N} samples")
        fresh = fresh[:FIRST_RUN_N]

    to_send = fresh[:MAX_PER_RUN]
    sent    = send_batch(to_send) if to_send else 0

    save_seen(seen)
    print(f"Done: {sent} sent / {len(fresh)} new / {len(items)} fetched")


if __name__ == "__main__":
    main()
