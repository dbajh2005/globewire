#!/usr/bin/env python3
"""
GLOBEWIRE - world news as planets around Earth.

Pulls RSS/Atom feeds from outlets around the world, sorts every story into
WORLD / POLITICS / FINANCE / TECH / SCIENCE, works out where in the world it is
happening, merges stories that several outlets carry, and flags stories whose
coverage jumps between updates as breaking. The page (globewire.html) draws
each story as a planet: direction from Earth = where, distance = how old,
size = how many outlets.

Standard library only. Python 3.9+.

  python3 globewire.py                     serve the page on http://localhost:8017
  python3 globewire.py --brief             print a 60-second brief in the terminal
  python3 globewire.py --export news.json  save a "tape" you can load into the hosted page
  python3 globewire.py --site site --previous https://you.github.io/globewire/news.json
                                           build a static site (used by the GitHub workflow)
"""
import argparse
import concurrent.futures as cf
import datetime as dt
import email.utils
import hashlib
import html
import json
import re
import sys
import threading
import time
import urllib.request
import webbrowser
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
UA = "Mozilla/5.0 (GlobeWire news receiver; +https://github.com)"

# (source name, category or None to auto-sort, default region, url)
FEEDS = [
    # WORLD
    ("BBC World", "world", None, "https://feeds.bbci.co.uk/news/world/rss.xml"),
    ("Al Jazeera", None, None, "https://www.aljazeera.com/xml/rss/all.xml"),
    ("Guardian World", "world", None, "https://www.theguardian.com/world/rss"),
    ("DW", None, "EUROPE", "https://rss.dw.com/rdf/rss-en-all"),
    ("France 24", None, None, "https://www.france24.com/en/rss"),
    ("NPR World", "world", None, "https://feeds.npr.org/1004/rss.xml"),
    ("BBC Asia", "world", "ASIA-PAC", "https://feeds.bbci.co.uk/news/world/asia/rss.xml"),
    ("BBC Africa", "world", "AFRICA", "https://feeds.bbci.co.uk/news/world/africa/rss.xml"),
    ("BBC Latin America", "world", "AMERICAS", "https://feeds.bbci.co.uk/news/world/latin_america/rss.xml"),
    ("BBC Middle East", "world", "MIDEAST", "https://feeds.bbci.co.uk/news/world/middle_east/rss.xml"),
    # POLITICS
    ("BBC Politics", "politics", "EUROPE", "https://feeds.bbci.co.uk/news/politics/rss.xml"),
    ("Guardian Politics", "politics", "EUROPE", "https://www.theguardian.com/politics/rss"),
    ("Guardian US Politics", "politics", "AMERICAS", "https://www.theguardian.com/us-news/us-politics/rss"),
    ("NPR Politics", "politics", "AMERICAS", "https://feeds.npr.org/1014/rss.xml"),
    ("Politico", "politics", "AMERICAS", "https://rss.politico.com/politics-news.xml"),
    # FINANCE
    ("BBC Business", "finance", None, "https://feeds.bbci.co.uk/news/business/rss.xml"),
    ("CNBC", "finance", "AMERICAS", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
    ("MarketWatch", "finance", "AMERICAS", "https://feeds.marketwatch.com/marketwatch/topstories/"),
    ("Guardian Business", "finance", None, "https://www.theguardian.com/business/rss"),
    ("NPR Business", "finance", "AMERICAS", "https://feeds.npr.org/1006/rss.xml"),
    # TECH
    ("BBC Technology", "tech", None, "https://feeds.bbci.co.uk/news/technology/rss.xml"),
    ("Ars Technica", "tech", None, "https://feeds.arstechnica.com/arstechnica/index"),
    ("The Verge", "tech", None, "https://www.theverge.com/rss/index.xml"),
    ("TechCrunch", "tech", None, "https://techcrunch.com/feed/"),
    ("Hacker News", "tech", "GLOBAL", "https://hnrss.org/frontpage"),
    ("Guardian Tech", "tech", None, "https://www.theguardian.com/technology/rss"),
    # SCIENCE & HEALTH
    ("BBC Science", "science", None, "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml"),
    ("BBC Health", "science", None, "https://feeds.bbci.co.uk/news/health/rss.xml"),
    ("ScienceDaily", "science", "GLOBAL", "https://www.sciencedaily.com/rss/top.xml"),
    ("Guardian Science", "science", None, "https://www.theguardian.com/science/rss"),
    ("NASA", "science", "GLOBAL", "https://www.nasa.gov/news-release/feed/"),
]

CATEGORY_WORDS = {
    "politics": "election elections parliament senate congress minister ministers president vote voters votes "
                "campaign party government policy law laws court supreme lawmakers legislation white house "
                "downing labour tory tories conservative democrat democrats republican republicans sanctions "
                "diplomat diplomatic summit treaty mp mps referendum coalition cabinet impeachment opposition",
    "finance": "market markets stocks stock shares economy economic inflation bank banks rates interest oil "
               "prices trade tariff tariffs earnings profit profits gdp investor investors dollar currency bond "
               "bonds jobs unemployment merger ipo crypto bitcoin revenue recession wall_street",
    "tech": "ai tech technology software app apps apple google microsoft meta amazon nvidia chip chips "
            "semiconductor cyber cyberattack hack hackers hacked robot robots startup internet smartphone "
            "openai anthropic quantum social media tiktok online data breach",
    "science": "science study research scientists space nasa climate health disease virus vaccine cancer "
               "species planet fossil ocean medical hospital drug brain emissions weather earthquake "
               "telescope astronomers outbreak",
}

# Places: (display name, region, longitude, keywords). A keyword starting with "=" is matched
# case-sensitively against the original text ("US" the country, not "us" the word);
# an underscore joins a multi-word phrase.
PLACES = [
    ("United States", "AMERICAS", -98, "=US =U.S. america american americans united_states washington white_house wall_street =Fed new_york california texas florida pentagon"),
    ("Canada", "AMERICAS", -106, "canada canadian ottawa toronto"),
    ("Mexico", "AMERICAS", -102, "mexico mexican"),
    ("Caribbean", "AMERICAS", -75, "cuba cuban haiti haitian jamaica caribbean"),
    ("Colombia", "AMERICAS", -74, "colombia colombian"),
    ("Venezuela", "AMERICAS", -66, "venezuela venezuelan"),
    ("Peru", "AMERICAS", -75, "peru peruvian ecuador"),
    ("Brazil", "AMERICAS", -51, "brazil brazilian"),
    ("Argentina", "AMERICAS", -64, "argentina argentine chile chilean bolivia"),
    ("United Kingdom", "EUROPE", -2, "=UK britain british england scotland wales london downing westminster bank_of_england"),
    ("Ireland", "EUROPE", -8, "ireland irish dublin"),
    ("Spain", "EUROPE", -4, "spain spanish madrid portugal"),
    ("France", "EUROPE", 2, "france french paris macron"),
    ("EU", "EUROPE", 4, "=EU european brussels =ECB =NATO nato netherlands dutch belgium"),
    ("Germany", "EUROPE", 10, "germany german berlin austria"),
    ("Italy", "EUROPE", 12, "italy italian rome"),
    ("Nordics", "EUROPE", 15, "sweden swedish norway denmark finland"),
    ("Poland", "EUROPE", 19, "poland polish hungary"),
    ("Greece", "EUROPE", 22, "greece greek romania balkans serbia"),
    ("Ukraine", "EUROPE", 31, "ukraine ukrainian kyiv zelensky"),
    ("Russia", "EUROPE", 37, "russia russian kremlin moscow putin"),
    ("West Africa", "AFRICA", -5, "nigeria nigerian ghana senegal mali sahel"),
    ("North Africa", "AFRICA", 5, "morocco algeria tunisia libya"),
    ("Africa", "AFRICA", 20, "africa african"),
    ("Central Africa", "AFRICA", 23, "congo cameroon chad"),
    ("Egypt", "AFRICA", 31, "egypt egyptian cairo"),
    ("Sudan", "AFRICA", 30, "sudan sudanese"),
    ("East Africa", "AFRICA", 38, "kenya ethiopia uganda tanzania rwanda somalia"),
    ("Southern Africa", "AFRICA", 26, "zimbabwe mozambique south_africa"),
    ("Turkey", "MIDEAST", 35, "turkey turkish ankara erdogan"),
    ("Israel", "MIDEAST", 35, "israel israeli netanyahu"),
    ("Gaza", "MIDEAST", 34, "gaza palestinian palestinians hamas west_bank"),
    ("Lebanon", "MIDEAST", 36, "lebanon lebanese hezbollah jordan syria syrian"),
    ("Iraq", "MIDEAST", 44, "iraq iraqi baghdad"),
    ("Gulf", "MIDEAST", 47, "saudi qatar =UAE dubai yemen houthi houthis"),
    ("Iran", "MIDEAST", 53, "iran iranian tehran"),
    ("Afghanistan", "ASIA-PAC", 67, "afghanistan afghan taliban"),
    ("Pakistan", "ASIA-PAC", 69, "pakistan pakistani"),
    ("India", "ASIA-PAC", 79, "india indian delhi modi bangladesh nepal sri_lanka"),
    ("Southeast Asia", "ASIA-PAC", 103, "myanmar thailand vietnam malaysia singapore indonesia cambodia"),
    ("China", "ASIA-PAC", 104, "china chinese beijing xi hong_kong"),
    ("Taiwan", "ASIA-PAC", 121, "taiwan taiwanese"),
    ("Philippines", "ASIA-PAC", 122, "philippines filipino manila"),
    ("Korea", "ASIA-PAC", 127, "korea korean seoul pyongyang"),
    ("Japan", "ASIA-PAC", 138, "japan japanese tokyo"),
    ("Australia", "ASIA-PAC", 134, "australia australian sydney canberra"),
    ("New Zealand", "ASIA-PAC", 174, "new_zealand pacific_islands"),
]


def _words(text):
    """Keywords are space-separated; an underscore joins a phrase (white_house)."""
    return [w.replace("_", " ") for w in text.split()]


def _rx(word):
    if word.startswith("="):
        return ("cs", re.compile(r"(?<![A-Za-z])" + re.escape(word[1:]) + r"(?![A-Za-z])"))
    return ("ci", re.compile(r"(?<![a-z])" + re.escape(word.lower()) + r"(?![a-z])"))


CATEGORY_RX = {k: [_rx(w) for w in v.split()] for k, v in CATEGORY_WORDS.items()}
PLACE_RX = [(name, region, lon, [_rx(w) for w in _words(words)]) for name, region, lon, words in PLACES]

STOP = set("the a an and or of to in on for with at by from as is are was were be been has have had it its "
           "this that after over into new says say said will would could may can not no than more about amid "
           "up out who what how why when you your our their his her they them just first last two three".split())


# ---------- feed parsing ----------

def _local(tag):
    return tag.rsplit("}", 1)[-1].lower() if isinstance(tag, str) else ""


def _text(el, *names):
    for c in el:
        if _local(c.tag) in names and (c.text or "").strip():
            return c.text.strip()
    return ""


def _link(el):
    for c in el:
        if _local(c.tag) == "link":
            href = c.get("href")
            if href and c.get("rel", "alternate") == "alternate":
                return href.strip()
            if (c.text or "").strip():
                return c.text.strip()
    guid = _text(el, "guid", "id")
    return guid if guid.startswith("http") else ""


def _date(s):
    if not s:
        return None
    try:
        d = email.utils.parsedate_to_datetime(s)
    except Exception:
        try:
            d = dt.datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
        except Exception:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d


def _clean(s, limit=300):
    s = html.unescape(re.sub(r"<[^>]+>", " ", s or ""))
    s = re.sub(r"\s+", " ", s).strip()
    if s.startswith("Article URL:"):  # Hacker News puts only links here
        return ""
    if len(s) > limit:
        s = s[:limit].rsplit(" ", 1)[0] + "…"
    return s


def parse_feed(raw):
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        # some feeds use HTML entities XML doesn't know (&nbsp; etc.)
        fixed = re.sub(rb"&(?!(amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)", b"&amp;", raw)
        root = ET.fromstring(fixed)
    items = []
    for el in root.iter():
        if _local(el.tag) not in ("item", "entry"):
            continue
        title = _clean(_text(el, "title"), 200)
        if not title:
            continue
        items.append({
            "title": title,
            "url": _link(el),
            "summary": _clean(_text(el, "description", "summary", "content")),
            "date": _date(_text(el, "pubdate", "published", "updated", "date")),
        })
    return items


def fetch(feed, timeout=12):
    name, cat, region, url = feed
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/rss+xml, application/xml, */*"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read(4_000_000)
        return feed, parse_feed(raw), None
    except Exception as e:  # one dead feed never stops the wire
        return feed, [], f"{type(e).__name__}: {e}"[:160]


# ---------- sorting ----------

def _count(patterns, text, low):
    return sum(1 for kind, p in patterns if p.search(text if kind == "cs" else low))


def _first(patterns, text, low):
    """Position of the earliest match, used to break ties: the place named first wins."""
    pos = [m.start() for kind, p in patterns for m in [p.search(text if kind == "cs" else low)] if m]
    return min(pos) if pos else 10**9


def classify_cat(text, default):
    low = " " + text.lower() + " "
    best, best_n = default, 0
    for key, patterns in CATEGORY_RX.items():
        n = _count(patterns, text, low)
        if n > best_n:
            best, best_n = key, n
    return best


def locate(text, default_region):
    """Return (region, place, longitude) for a story, or the feed's default region with no place."""
    low = " " + text.lower() + " "
    hits = [(name, region, lon, _count(pats, text, low), _first(pats, text, low)) for name, region, lon, pats in PLACE_RX]
    hits = [h for h in hits if h[3]]
    if not hits:
        return default_region or "GLOBAL", None, None
    by_region = {}
    for name, region, lon, n, at in hits:
        n0, at0 = by_region.get(region, (0, 10**9))
        by_region[region] = (n0 + n, min(at0, at))
    region = max(by_region, key=lambda r: (by_region[r][0], -by_region[r][1]))
    name, _, lon, _, _ = max((h for h in hits if h[1] == region), key=lambda h: (h[3], -h[4]))
    return region, name, lon


def tokens(title):
    return {w for w in re.findall(r"[a-z0-9']+", title.lower()) if len(w) > 2 and w not in STOP}


def load_previous(where):
    """Read the last published news.json (path or URL) so breaking stories can be spotted."""
    if not where:
        return {}
    try:
        if re.match(r"https?://", where):
            req = urllib.request.Request(where + ("&" if "?" in where else "?") + "t=" + str(int(time.time())),
                                         headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read())
        return json.loads(Path(where).read_text(encoding="utf-8"))
    except Exception as e:
        print(f"No previous sky ({type(e).__name__}); breaking flags start next update.", file=sys.stderr)
        return {}


BREAKING_WINDOW_MS = 90 * 60 * 1000


def build(previous=None, max_age_hours=48, per_feed=25, workers=16):
    now = dt.datetime.now(dt.timezone.utc)
    now_ms = int(now.timestamp() * 1000)
    raw_items, sources = [], []
    with cf.ThreadPoolExecutor(max_workers=workers) as pool:
        for feed, items, err in pool.map(fetch, FEEDS):
            name, fixed_cat, default_region, _ = feed
            kept = 0
            for it in items[:per_feed]:
                d = it["date"] or now
                if (now - d).total_seconds() > max_age_hours * 3600:
                    continue
                text = it["title"] + " " + it["summary"]
                region, place, lon = locate(text, default_region)
                raw_items.append({
                    **it,
                    "ts": min(now_ms, int(d.timestamp() * 1000)),
                    "source": name,
                    "cat": fixed_cat or classify_cat(text, "world"),
                    "fixed": bool(fixed_cat),
                    "region": region, "place": place, "lon": lon,
                    "tok": tokens(it["title"]),
                })
                kept += 1
            sources.append({"name": name, "cat": fixed_cat or "auto", "ok": err is None, "count": kept, "error": err})

    # merge stories several outlets carry
    raw_items.sort(key=lambda x: -x["ts"])
    clusters = []
    for it in raw_items:
        for c in clusters:
            inter = len(it["tok"] & c["tok"])
            union = len(it["tok"] | c["tok"]) or 1
            if inter >= 3 and inter / union >= 0.34:
                c["items"].append(it)
                break
        else:
            clusters.append({"tok": set(it["tok"]), "items": [it]})

    # what the last update looked like, for breaking-news detection
    previous = previous or {}
    prev_stories = previous.get("stories") or []
    have_baseline = bool(prev_stories)
    prev_by_id = {p.get("id"): p for p in prev_stories}
    prev_by_url = {}
    for p in prev_stories:
        for x in (p.get("items") or [{"url": p.get("url")}]):
            if x.get("url"):
                prev_by_url[x["url"]] = p

    stories = []
    for c in clusters:
        items = c["items"]
        lead = max(items, key=lambda x: (len(x["summary"]) > 40, x["ts"]))
        per_source, seen = [], set()
        for x in items:
            if x["source"] not in seen:
                seen.add(x["source"])
                per_source.append({"source": x["source"], "title": x["title"], "url": x["url"], "ts": x["ts"]})
        cats = [x["cat"] for x in items if x["fixed"]] or [x["cat"] for x in items]
        located = [x for x in items if x["region"] != "GLOBAL"]
        regions = [x["region"] for x in located] or ["GLOBAL"]
        region = max(set(regions), key=regions.count)
        placed = [x for x in located if x["region"] == region and x["place"]]
        place_names = [x["place"] for x in placed]
        place = max(set(place_names), key=place_names.count) if place_names else None
        lon = next((x["lon"] for x in placed if x["place"] == place), None)
        story = {
            "id": hashlib.sha1((lead["url"] or lead["title"]).encode()).hexdigest()[:10],
            "title": lead["title"],
            "summary": lead["summary"],
            "url": lead["url"],
            "source": lead["source"],
            "sources": [p["source"] for p in per_source],
            "items": per_source,
            "signal": len(per_source),
            "cat": max(set(cats), key=cats.count),
            "region": region, "place": place, "lon": lon,
            "ts": max(x["ts"] for x in items),
        }
        prev = prev_by_id.get(story["id"]) or next((prev_by_url[p["url"]] for p in per_source if p["url"] in prev_by_url), None)
        story["first_seen"] = (prev or {}).get("first_seen") or now_ms
        story["prev_signal"] = (prev or {}).get("signal") or 0
        jumped = have_baseline and (story["signal"] - story["prev_signal"] >= 2 or (prev is None and story["signal"] >= 3))
        since = (prev or {}).get("breaking_since")
        if jumped:
            since = now_ms
        elif since and now_ms - since > BREAKING_WINDOW_MS:
            since = None
        story["breaking_since"] = since
        story["breaking"] = since is not None
        stories.append(story)

    def score(s):
        age_h = (now_ms - s["ts"]) / 3.6e6
        return s["signal"] * 3 + max(0, 24 - age_h) / 6 + (4 if s["breaking"] else 0)

    stories.sort(key=score, reverse=True)
    return {
        "app": "globewire",
        "version": 2,
        "generated": now.isoformat(timespec="seconds"),
        "sources": sources,
        "stories": stories,
    }


# ---------- terminal brief ----------

ANSI = {"world": "96", "politics": "91", "finance": "93", "tech": "95", "science": "92"}
NAMES = {"world": "WORLD", "politics": "POLITICS", "finance": "FINANCE", "tech": "TECH", "science": "SCIENCE"}


def brief(data, n=7):
    picks, seen = [], set()
    for cat in NAMES:
        for s in data["stories"]:
            if s["cat"] == cat:
                picks.append(s)
                seen.add(s["id"])
                break
    for s in data["stories"]:
        if len(picks) >= n:
            break
        if s["id"] not in seen:
            picks.append(s)
            seen.add(s["id"])
    ok = sum(1 for x in data["sources"] if x["ok"])
    print(f"\033[44;97m GLOBEWIRE · 60-SECOND FLYBY \033[0m  {ok}/{len(data['sources'])} feeds on air\n")
    for s in picks:
        bars = "●" * min(s["signal"], 5) + "○" * (5 - min(s["signal"], 5))
        flag = " \033[41;97m BREAKING \033[0m" if s.get("breaking") else ""
        print(f"\033[{ANSI[s['cat']]}m{NAMES[s['cat']]:<9}\033[0m {bars}{flag}  \033[1m{s['title']}\033[0m")
        if s["summary"]:
            print(f"          {s['summary'][:140]}")
        where = s.get("place") or s["region"].title()
        print(f"          \033[2m{s['source']} · {where} · {s['url']}\033[0m\n")


# ---------- page ----------

def page_html():
    """globewire.html is written for claude.ai, which adds the document skeleton. Add it here too."""
    body = (HERE / "globewire.html").read_bytes()
    if body.lstrip().lower().startswith(b"<!doctype"):
        return body
    return (b'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            b'<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
            b'</head><body>' + body + b'</body></html>')


def build_site(out_dir, previous):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    data = build(previous)
    (out / "news.json").write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    (out / "index.html").write_bytes(page_html())
    ok = sum(1 for s in data["sources"] if s["ok"])
    hot = sum(1 for s in data["stories"] if s["breaking"])
    print(f"Site built in {out}: {ok}/{len(data['sources'])} feeds on air, {len(data['stories'])} stories, {hot} breaking.")
    if ok == 0:
        sys.exit("Every feed failed, so the site was not updated.")


# ---------- local server ----------

class Wire:
    def __init__(self, ttl):
        self.ttl, self.lock, self.data, self.at = ttl, threading.Lock(), None, 0.0

    def get(self, force=False):
        with self.lock:
            if force or self.data is None or time.time() - self.at > self.ttl:
                self.data = build(self.data)  # the last update is the baseline for breaking news
                self.at = time.time()
            return self.data


def serve(port, ttl, open_browser):
    if not (HERE / "globewire.html").exists():
        sys.exit("globewire.html must sit next to globewire.py")
    wire = Wire(ttl)

    class Handler(BaseHTTPRequestHandler):
        def _send(self, body, ctype, code=200):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path, _, query = self.path.partition("?")
            if path in ("/", "/index.html", "/globewire.html"):
                self._send(page_html(), "text/html; charset=utf-8")
            elif path == "/news.json":
                data = wire.get(force="refresh=1" in query)
                self._send(json.dumps(data).encode(), "application/json")
            else:
                self._send(b"not found", "text/plain", 404)

        def log_message(self, *a):
            pass

    print("Tuning in to", len(FEEDS), "feeds…")
    data = wire.get()
    ok = sum(1 for s in data["sources"] if s["ok"])
    print(f"{ok}/{len(FEEDS)} feeds on air, {len(data['stories'])} stories.")
    url = f"http://localhost:{port}/"
    print("GLOBEWIRE is live at", url, "(Ctrl+C to stop)")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


def main():
    ap = argparse.ArgumentParser(description="GLOBEWIRE world news receiver")
    ap.add_argument("--port", type=int, default=8017)
    ap.add_argument("--ttl", type=int, default=600, help="seconds before the local server refetches (default 600)")
    ap.add_argument("--export", metavar="FILE", help="write a news tape (JSON) and exit")
    ap.add_argument("--site", metavar="DIR", help="write index.html and news.json for static hosting and exit")
    ap.add_argument("--previous", metavar="PATH_OR_URL", help="last news.json, used to spot breaking stories")
    ap.add_argument("--brief", action="store_true", help="print a 60-second brief and exit")
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    if a.site:
        build_site(a.site, load_previous(a.previous))
        return
    if a.export or a.brief:
        data = build(load_previous(a.previous))
        if a.export:
            Path(a.export).write_text(json.dumps(data, indent=1), encoding="utf-8")
            print(f"Tape saved: {a.export} ({len(data['stories'])} stories)")
        if a.brief:
            brief(data)
        return
    try:
        serve(a.port, a.ttl, not a.no_browser)
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
