#!/usr/bin/env python3
"""
TREMOR - a seismograph for the world's news.

GLOBEWIRE merges the same story from several outlets into one "development".
TREMOR follows developments over days and groups them into situations: ongoing
stories such as a ceasefire negotiation or a run of interest-rate decisions.
Each development gets a magnitude, each situation keeps up to three weeks of
history, and the page (tremor/index.html) draws every situation as a
seismograph trace.

Standard library only. Python 3.9+. The optional AI step needs `pip install anthropic`
and an ANTHROPIC_API_KEY; without them TREMOR still works, using headlines as-is.

  python3 tremor.py                    fetch, track and serve http://localhost:8018 (refreshes every 10 min)
  python3 tremor.py --news site/news.json --previous STATE --site site/tremor
                                       one update for static hosting (used by the GitHub workflow)
"""
import argparse
import datetime as dt
import hashlib
import json
import math
import os
import re
import sys
import threading
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import globewire as gw

HERE = Path(__file__).resolve().parent
PAGE = HERE / "tremor" / "index.html"

HOUR = 3600 * 1000
DAY = 24 * HOUR
KEEP_EVENTS = 21 * DAY          # history kept per situation
DROP_QUIET = 14 * DAY           # a situation with no news for this long is dropped
JOIN_WINDOW = 10 * DAY          # a new development can join a situation active this recently
JOIN_SIMILARITY = 0.30
MAX_SITUATIONS = 400
MAX_EVENTS = 60

GENERIC = set("""
says say said new news report reports reported after amid over could would will more first year years week weeks
day days live update updates latest video watch how why what who get gets make makes set back top big may might
call calls plan plans time times just still one two three four five six seven eight nine ten million billion
per cent percent former told tells against into out off down near also than then there their they them this that
these those with from have has had been being were was are is its his her our your you not but and for the
""".split())
STOP = gw.STOP | GENERIC
OUTLET_PREFIXES = ("BBC", "Guardian", "NPR")


def outlet(source):
    """BBC World and BBC Asia are one outlet, not two."""
    for p in OUTLET_PREFIXES:
        if source.startswith(p):
            return p
    return source


def words(text):
    return re.findall(r"[A-Za-z][A-Za-z'’\-]*[A-Za-z]|[A-Za-z]", text or "")


def stem(w):
    """Light stemming so "floods", "flooded" and "flood" match."""
    for suffix in ("ing", "ed", "es", "s"):
        if w.endswith(suffix) and len(w) - len(suffix) >= 4:
            w = w[: -len(suffix)]
            break
    return w[:-1] if w.endswith("e") and len(w) > 4 else w


def toks(text):
    out = []
    for w in words(text):
        for part in w.lower().replace("’", "'").split("-"):
            part = part.strip("'")
            if part.endswith("'s"):
                part = part[:-2]
            if len(part) > 2 and part not in STOP:
                out.append(stem(part))
    return out


def now_ms():
    return int(time.time() * 1000)


def iso(ms):
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).isoformat(timespec="seconds")


# ---------- similarity ----------

def idf_table(docs):
    df = Counter()
    for d in docs:
        df.update(set(d))
    n = len(docs)
    return lambda t: math.log((n + 1) / (df.get(t, 0) + 1)) + 0.5


def vector(event, idf):
    """Headlines only: summaries add words about the reporting, not the topic."""
    tf = Counter()
    for item in event["items"]:
        tf.update(toks(item["title"]))
    return {t: c * idf(t) for t, c in tf.items()}


def cosine(a, b):
    if not a or not b:
        return 0.0
    dot = sum(v * b.get(k, 0.0) for k, v in a.items())
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na and nb else 0.0


def merge_keys(keys, vec, decay=0.8, keep=40):
    out = {k: v * decay for k, v in (keys or {}).items()}
    for k, v in vec.items():
        out[k] = out.get(k, 0.0) + v
    top = sorted(out.items(), key=lambda kv: -kv[1])[:keep]
    return {k: round(v, 3) for k, v in top}


# ---------- magnitude ----------

def magnitude(items, followup):
    """Independent outlets, how fast it spread, and whether it moved an existing situation."""
    outlets = {outlet(i["source"]) for i in items}
    first = min(i["ts"] for i in items)
    quick = {outlet(i["source"]) for i in items if i["ts"] - first <= 3 * HOUR}
    m = 1.5 + 1.6 * math.log2(1 + len(outlets))
    if len(quick) >= 3:
        m += 0.4
    if followup:
        m += 0.3
    return round(min(9.0, m), 1)


# ---------- reading the text ----------

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december"]
MON_RX = "|".join(MONTHS + [m[:3] for m in MONTHS if m != "may"])
FUTURE_CUES = re.compile(r"\b(will|set to|set for|is set|are set|scheduled|due to|due on|expected|deadline|upcoming|"
                         r"next|plans? to|to vote|to meet|to decide|to rule|to announce|to publish|to hold|"
                         r"go to the polls|polls open|will be held|takes place|goes on sale)\b", re.I)
DATE_RXS = [
    ("weekday", re.compile(r"\b(?:on|by|until|before|next|this|from)\s+(" + "|".join(WEEKDAYS) + r")\b", re.I)),
    ("daymonth", re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(" + MON_RX + r")\b", re.I)),
    ("monthday", re.compile(r"\b(" + MON_RX + r")\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b", re.I)),
    ("tomorrow", re.compile(r"\btomorrow\b", re.I)),
    ("nextweek", re.compile(r"\bnext week\b", re.I)),
]


def _month(name):
    name = name.lower()[:3]
    return [m[:3] for m in MONTHS].index(name) + 1


def sentences(text):
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text or "") if s.strip()]


def find_ahead(events, now):
    """Dates mentioned in the reporting that are still ahead, with the sentence they came from."""
    found = []
    for e in events[-6:]:
        base = dt.datetime.fromtimestamp(e["t"] / 1000, dt.timezone.utc)
        for text in [e["title"]] + sentences(e.get("summary", "")):
            if not FUTURE_CUES.search(text):
                continue
            for kind, rx in DATE_RXS:
                m = rx.search(text)
                if not m:
                    continue
                try:
                    if kind == "weekday":
                        wd = WEEKDAYS.index(m.group(1).lower())
                        delta = (wd - base.weekday()) % 7 or 7
                        when = base + dt.timedelta(days=delta)
                    elif kind in ("daymonth", "monthday"):
                        day, mon = (m.group(1), m.group(2)) if kind == "daymonth" else (m.group(2), m.group(1))
                        when = base.replace(month=_month(mon), day=int(day))
                        if when < base - dt.timedelta(days=30):
                            when = when.replace(year=when.year + 1)
                    elif kind == "tomorrow":
                        when = base + dt.timedelta(days=1)
                    else:
                        when = base + dt.timedelta(days=7)
                except (ValueError, IndexError):
                    continue
                when = when.replace(hour=12, minute=0, second=0, microsecond=0)
                ms = int(when.timestamp() * 1000)
                if now < ms <= now + 45 * DAY:
                    label = text if len(text) <= 110 else text[:107].rsplit(" ", 1)[0] + "…"
                    found.append({"t": ms, "label": label, "url": e.get("url", ""), "exact": kind != "nextweek"})
                break
    seen, out = set(), []
    for f in sorted(found, key=lambda f: f["t"]):
        key = (f["t"] // DAY, f["label"][:30])
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out[:3]


NUM = r"(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)"
SCALE = {"million": 1e6, "m": 1e6, "billion": 1e9, "bn": 1e9, "thousand": 1e3, "k": 1e3}
NOUNS = (r"(people|residents|civilians|children|refugees|migrants|displaced|families|households|dead|killed|"
         r"deaths|injured|wounded|evacuated|homeless|affected|patients|survivors|inhabitants)")
IMPACT_RXS = [
    re.compile(NUM + r"\s*(million|billion|thousand|bn|m|k)?\s+(?:more\s+|other\s+|of\s+the\s+)?" + NOUNS, re.I),
    re.compile(r"\b(hundreds of thousands|tens of thousands|thousands|millions|hundreds)\s+of\s+" + NOUNS, re.I),
    re.compile(NUM + r"\s*(million|billion|thousand|bn|m|k)?\s+(?:people\s+)?(?:are\s+|now\s+)?(facing|face|at risk|in need|need|without)\b", re.I),
]
# a number only counts as "people affected" in a sentence about harm, not "40 million people voted"
HARM = re.compile(r"displac|flee|fled|evacuat|kill|dead|death|died|injur|wound|hunger|famine|starv|homeless|shelter|"
                  r"\baid\b|affected|at risk|outbreak|cases|infect|flood|drought|quake|storm|cyclone|hurricane|"
                  r"\bwar\b|fighting|violence|refugee|cholera|without power|without water|malnutrition|disease", re.I)
VAGUE = {"hundreds of thousands": 2e5, "tens of thousands": 2e4, "thousands": 2e3, "millions": 2e6, "hundreds": 200}


def find_impact(events):
    """The largest number of people the reporting says are affected, with the phrase it came from."""
    best = None
    for e in events:
        for text in [e["title"]] + sentences(e.get("summary", "")):
            if not HARM.search(text):
                continue
            for rx in IMPACT_RXS:
                for m in rx.finditer(text):
                    if m.group(1).lower() in VAGUE:
                        n = VAGUE[m.group(1).lower()]
                    else:
                        n = float(m.group(1).replace(",", "")) * SCALE.get((m.group(2) or "").lower(), 1)
                    if (m.group(m.lastindex) or "").lower() in ("families", "households"):
                        n *= 4
                    if n < 10 or n > 9e9:
                        continue
                    if not best or n > best["people"]:
                        quote = text if len(text) <= 160 else text[:157].rsplit(" ", 1)[0] + "…"
                        best = {"people": int(n), "quote": quote, "url": e.get("url", "")}
    return best


HEADLINE_VERBS = set("""tops beats hits wins says faces gets takes makes sees plans seeks warns urges backs slams vows eyes
rises falls jumps drops surges slips rallies launches unveils announces agrees signals holds keeps leaves survives
expands doubles finds completes releases leads enters breaks reaches calls cuts raises sets marks""".split())
SHORT_PLACE = {"United States": "US", "United Kingdom": "UK"}


def heuristic_name(s):
    """A short name from the phrase that keeps coming back in the headlines."""
    titles = [i["title"] for e in s["events"][-12:] for i in e["items"]]
    pairs, shown = Counter(), {}
    for title in titles:
        ws, seen = words(title), set()
        for a, b in zip(ws, ws[1:]):
            la, lb = a.lower(), b.lower()
            if la in STOP or lb in STOP or len(la) < 3 or len(lb) < 3 or la in HEADLINE_VERBS or lb in HEADLINE_VERBS:
                continue
            key = la + " " + lb
            if key not in seen:
                seen.add(key)
                pairs[key] += 1
                shown.setdefault(key, a + " " + b)
    place = SHORT_PLACE.get(s.get("place"), s.get("place"))
    if pairs:
        key, n = pairs.most_common(1)[0]
        if n >= 2:
            phrase = shown[key]
            phrase = phrase[0].upper() + phrase[1:]
            return f"{place}: {phrase}" if place and place.lower() not in key else phrase
    # nothing repeats yet: use the best-covered headline, shortened
    lead = max(s["events"], key=lambda e: (e["outlets"], e["t"]))["title"]
    return lead if len(lead) <= 60 else lead[:57].rsplit(" ", 1)[0] + "…"


def mode(values, default=None):
    values = [v for v in values if v]
    return Counter(values).most_common(1)[0][0] if values else default


# ---------- the update ----------

def update(news, previous, now, ai=True):
    situations = [s for s in (previous.get("situations") or []) if s.get("events")]
    by_url = {}
    for s in situations:
        for e in s["events"]:
            for u in e.get("urls", []):
                by_url[u] = (s, e)

    docs = [toks(" ".join(i["title"] for i in st.get("items", []) or [{"title": st["title"]}])) for st in news.get("stories", [])]
    for s in situations:
        for e in s["events"][-5:]:
            docs.append(toks(e["title"]))
    idf = idf_table(docs)

    touched, fresh_events = set(), 0
    stories = sorted(news.get("stories", []), key=lambda st: min([i.get("ts", st["ts"]) for i in st.get("items", [])] or [st["ts"]]))
    for st in stories:
        items = [{"source": i["source"], "title": i["title"], "url": i.get("url", ""), "ts": min(now, int(i.get("ts") or st["ts"]))}
                 for i in (st.get("items") or [{"source": st["source"], "title": st["title"], "url": st["url"], "ts": st["ts"]}])]
        urls = [i["url"] for i in items if i["url"]]
        hit = next((by_url[u] for u in urls if u in by_url), None)
        if hit:
            s, e = hit
            known = {i["url"] or i["source"] for i in e["items"]}
            added = [i for i in items if (i["url"] or i["source"]) not in known]
            if added:
                e["items"] += added
                e["urls"] = sorted(set(e["urls"]) | {i["url"] for i in added if i["url"]})
                e["outlets"] = len({outlet(i["source"]) for i in e["items"]})
                e["m"] = max(e["m"], magnitude(e["items"], e.get("followup", False)))
                e["upd"] = now
                for i in added:
                    if i["url"]:
                        by_url[i["url"]] = (s, e)
                s["keys"] = merge_keys(s.get("keys"), vector({"items": added}, idf), decay=1.0)
                touched.add(s["id"])
            continue

        event = {
            "id": hashlib.sha1((urls[0] if urls else st["title"]).encode()).hexdigest()[:10],
            "t": min(i["ts"] for i in items), "seen": now, "upd": now,
            "title": st["title"], "summary": st.get("summary", ""), "url": st.get("url", ""), "source": st.get("source", ""),
            "items": items, "urls": urls, "outlets": len({outlet(i["source"]) for i in items}),
            "cat": st.get("cat", "world"), "region": st.get("region", "GLOBAL"), "place": st.get("place"),
        }
        vec = vector(event, idf)
        best, best_sim = None, 0.0
        for s in situations:
            if event["t"] - s["updated"] > JOIN_WINDOW:
                continue
            sim = cosine(vec, s.get("keys", {}))
            if s.get("place") and event["place"] and s["place"] != event["place"]:
                sim *= 0.6
            shared = len(set(vec) & set(s.get("keys", {})))
            if shared >= 2 and sim > best_sim:
                best, best_sim = s, sim
        if best and best_sim >= JOIN_SIMILARITY:
            s = best
            event["followup"] = True
        else:
            s = {"id": "s" + hashlib.sha1(event["id"].encode()).hexdigest()[:8], "events": [], "keys": {},
                 "created": event["t"], "updated": event["t"]}
            situations.append(s)
            event["followup"] = False
        event["m"] = magnitude(items, event["followup"])
        s["events"].append(event)
        s["events"].sort(key=lambda e: e["t"])
        s["keys"] = merge_keys(s.get("keys"), vec)
        s["updated"] = max(s["updated"], event["t"])
        for u in urls:
            by_url[u] = (s, event)
        touched.add(s["id"])
        fresh_events += 1

    # trim history and settle each situation's description
    kept = []
    for s in situations:
        s["events"] = [e for e in s["events"] if now - e["t"] <= KEEP_EVENTS][-MAX_EVENTS:]
        if not s["events"] or now - s["events"][-1]["t"] > DROP_QUIET:
            continue
        evs = s["events"]
        s["created"] = min(s.get("created", evs[0]["t"]), evs[0]["t"])
        s["updated"] = evs[-1]["t"]
        weighted = [e["cat"] for e in evs for _ in range(e["outlets"])]
        s["desk"] = mode(weighted, "world")
        located = [e for e in evs if e.get("region") and e["region"] != "GLOBAL"]
        s["region"] = mode([e["region"] for e in located], "GLOBAL")
        s["place"] = mode([e.get("place") for e in located if e["region"] == s["region"]])
        if s["id"] in touched or "name" not in s:
            s["name"] = heuristic_name(s)
            s["ahead"] = find_ahead(evs, now)
            s["impact"] = find_impact(evs[-12:])
        else:
            s["ahead"] = [a for a in s.get("ahead", []) if a["t"] > now]
        kept.append(s)
    kept.sort(key=lambda s: -s["updated"])
    situations = kept[:MAX_SITUATIONS]

    # situations that tend to move together: related terms, same place
    active = [s for s in situations if now - s["updated"] <= 7 * DAY][:200]
    for s in active:
        scored = []
        for o in active:
            if o is s:
                continue
            sim = cosine(s["keys"], o["keys"])
            same_place = bool(s.get("place") and s.get("place") == o.get("place"))
            if 0.08 <= sim < JOIN_SIMILARITY or (same_place and sim >= 0.03):
                scored.append((sim + (0.05 if same_place else 0), o["id"], "same place" if same_place and sim < 0.12 else "shares key terms"))
        s["related"] = [{"id": i, "why": why} for _, i, why in sorted(scored, reverse=True)[:3]]

    enriched = enrich(situations, touched, now) if ai else 0
    sources = news.get("sources") or []
    return {
        "app": "tremor", "version": 1, "generated": iso(now), "now": now,
        "feeds_ok": sum(1 for x in sources if x.get("ok")), "feeds_total": len(sources),
        "new_events": fresh_events, "ai": enriched > 0 or any(s.get("ai") for s in situations),
        "situations": situations,
    }


# ---------- optional: Claude writes the names, state lines and claims ----------

AI_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "state": {"type": "string"},
        "was": {"type": "string"},
        "claims": {"type": "array", "items": {
            "type": "object",
            "properties": {"text": {"type": "string"},
                           "support": {"type": "array", "items": {"type": "string"}},
                           "dispute": {"type": "array", "items": {"type": "string"}}},
            "required": ["text", "support", "dispute"], "additionalProperties": False}},
        "framing": {"type": "string"},
        "ahead": {"type": "array", "items": {
            "type": "object",
            "properties": {"date": {"type": "string"}, "label": {"type": "string"}},
            "required": ["date", "label"], "additionalProperties": False}},
        "people_affected": {"type": "integer"},
        "people_quote": {"type": "string"},
    },
    "required": ["name", "state", "was", "claims", "framing", "ahead", "people_affected", "people_quote"],
    "additionalProperties": False,
}

AI_SYSTEM = """You keep the record for TREMOR, a news app that follows ongoing situations.
You get the recent developments of one situation: headlines from named outlets, with summaries and times.
Use only that text. Never add facts, names, numbers or context that are not in it.

Return:
- name: what the ongoing situation is, in 2 to 5 words (for example "Gaza ceasefire talks" or "US interest rates"). Name the situation, not its latest event.
- state: where things stand now, in one plain sentence of at most 20 words, based on the latest development.
- was: where things stood just before the latest development, in one sentence, or "" if there is no earlier development.
- claims: 2 to 4 separate factual claims made in the latest development. For each, list in "support" the outlets whose headline or summary states it, and in "dispute" any that contradict it. Use outlet names exactly as given.
- framing: one sentence on how the outlets' headlines differ in emphasis, or "" if only one outlet reported it.
- ahead: up to 3 upcoming events the text explicitly says are scheduled or expected. Give the date as YYYY-MM-DD when it can be worked out from the article time, otherwise "".
- people_affected: the largest number of people the text says are affected, displaced, killed or at risk, or 0 if it gives no number. people_quote: the phrase that number comes from, or ""."""


def enrich(situations, touched, now):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return 0
    try:
        import anthropic
    except ImportError:
        print("ANTHROPIC_API_KEY is set but the anthropic package isn't installed; skipping summaries.", file=sys.stderr)
        return 0
    model = os.environ.get("TREMOR_MODEL") or "claude-opus-5-5"
    limit = int(os.environ.get("TREMOR_AI_MAX") or 4)
    every = float(os.environ.get("TREMOR_AI_EVERY_HOURS") or 3) * HOUR

    def due(s):
        latest = s["events"][-1]
        last = (s.get("ai") or {}).get("at", 0)
        return (s["id"] in touched and latest["seen"] > last and now - last >= every
                and max(e["m"] for e in s["events"][-3:]) >= 4.0)

    queue = sorted((s for s in situations if due(s)), key=lambda s: -s["events"][-1]["m"])[:limit]
    if not queue:
        return 0
    client = anthropic.Anthropic()
    use_fallback = not model.startswith("claude-haiku")
    done = 0
    for s in queue:
        recent = s["events"][-8:]
        outlets = sorted({outlet(i["source"]) for i in recent[-1]["items"]})
        payload = {
            "now": iso(now),
            "developments": [{
                "time": iso(e["t"]),
                "headlines": [{"outlet": outlet(i["source"]), "headline": i["title"]} for i in e["items"][:8]],
                "summary": e.get("summary", ""),
            } for e in recent],
            "outlets_on_latest_development": outlets,
        }
        request = dict(
            model=model, max_tokens=8000, system=AI_SYSTEM,
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": AI_SCHEMA}},
            messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        )
        try:
            if use_fallback:
                resp = client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **request)
            else:
                resp = client.messages.create(**request)
        except anthropic.RateLimitError:
            print("Rate limited; the rest of the summaries wait for the next update.", file=sys.stderr)
            break
        except anthropic.APIConnectionError as e:
            print(f"Couldn't reach the Claude API: {e}", file=sys.stderr)
            break
        except anthropic.APIStatusError as e:
            print(f"Claude API error {e.status_code} for {s['id']}: {e.message}", file=sys.stderr)
            continue
        if resp.stop_reason == "refusal":
            continue
        text = next((b.text for b in resp.content if b.type == "text"), "")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            continue
        known = set(outlets)
        data["claims"] = [{"text": c["text"], "support": [o for o in c["support"] if o in known],
                           "dispute": [o for o in c["dispute"] if o in known]} for c in data.get("claims", [])][:4]
        data["at"] = now
        data["model"] = getattr(resp, "model", model)
        s["ai"] = data
        done += 1
    print(f"Summaries written for {done} situation(s) with {model}.")
    return done


# ---------- loading and saving ----------

def newest_previous(sources):
    best = {}
    for src in sources or []:
        data = gw.load_previous(src)
        if data.get("app") == "tremor" and data.get("generated", "") > best.get("generated", ""):
            best = data
    return best


def write_site(out_dir, state):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "state.json").write_text(json.dumps(state, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    (out / "index.html").write_bytes(PAGE.read_bytes())


def summary_line(state):
    moved = sum(1 for s in state["situations"] if s["events"] and s["events"][-1]["seen"] == state["now"])
    return (f"{len(state['situations'])} situations, {state['new_events']} new developments, "
            f"{moved} situations moved, feeds {state['feeds_ok']}/{state['feeds_total']}.")


def serve(port, ttl):
    store = HERE / "tremor-state.json"
    lock = threading.Lock()
    box = {"state": newest_previous([str(store)]) if store.exists() else {}, "news": None, "at": 0.0}

    def refresh(force=False):
        with lock:
            if force or not box["state"].get("situations") or time.time() - box["at"] > ttl:
                box["news"] = gw.build(box["news"])
                box["state"] = update(box["news"], box["state"], now_ms())
                box["at"] = time.time()
                store.write_text(json.dumps(box["state"], ensure_ascii=False), encoding="utf-8")
                print(summary_line(box["state"]))
            return box["state"]

    class Handler(BaseHTTPRequestHandler):
        def _send(self, body, ctype, code=200):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path, _, query = self.path.partition("?")
            if path in ("/", "/index.html"):
                self._send(PAGE.read_bytes(), "text/html; charset=utf-8")
            elif path == "/state.json":
                self._send(json.dumps(refresh("refresh=1" in query), ensure_ascii=False).encode(), "application/json")
            else:
                self._send(b"not found", "text/plain", 404)

        def log_message(self, *a):
            pass

    print("Tuning in…")
    refresh()
    print(f"TREMOR is live at http://localhost:{port}/ (Ctrl+C to stop)")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


def main():
    ap = argparse.ArgumentParser(description="TREMOR: a seismograph for the world's news")
    ap.add_argument("--news", help="news.json from globewire.py (this update's developments)")
    ap.add_argument("--previous", action="append", help="last state.json, as a path or URL; repeat to give a backup")
    ap.add_argument("--site", help="folder to write index.html and state.json into")
    ap.add_argument("--save", help="also write the state here (the workflow keeps it as a backup)")
    ap.add_argument("--now", type=int, help="pretend it is this time (ms since 1970); for testing")
    ap.add_argument("--no-ai", action="store_true", help="skip the Claude summaries even if a key is set")
    ap.add_argument("--port", type=int, default=8018)
    ap.add_argument("--ttl", type=int, default=600)
    a = ap.parse_args()

    if not a.news:
        try:
            serve(a.port, a.ttl)
        except KeyboardInterrupt:
            print("\nStopped.")
        return

    previous = newest_previous(a.previous)
    try:
        news = json.loads(Path(a.news).read_text(encoding="utf-8"))
        state = update(news, previous, a.now or now_ms(), ai=not a.no_ai)
        print("TREMOR:", summary_line(state))
    except Exception as e:  # never take the site down: republish the last good state instead
        print(f"TREMOR update failed ({type(e).__name__}: {e}); keeping the previous state.", file=sys.stderr)
        state = previous
        if not state:
            raise
    if a.site:
        write_site(a.site, state)
    if a.save:
        Path(a.save).write_text(json.dumps(state, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
