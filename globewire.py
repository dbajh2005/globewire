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
import os
import re
import sys
import threading
import time
import unicodedata
import urllib.request
import webbrowser
import xml.etree.ElementTree as ET
from collections import Counter
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
    ("Sky News", None, None, "https://feeds.skynews.com/feeds/rss/world.xml"),
    ("CBS News", None, None, "https://www.cbsnews.com/latest/rss/world"),
    ("Euronews", None, "EUROPE", "https://www.euronews.com/rss?level=theme&name=news"),
    # REGIONAL VOICES: outlets based in the regions they cover
    ("South China Morning Post", None, None, "https://www.scmp.com/rss/91/feed"),
    ("CNA", None, "ASIA-PAC", "https://www.channelnewsasia.com/api/v1/rss-outbound-feed?_format=xml"),
    ("The Hindu", None, "ASIA-PAC", "https://www.thehindu.com/news/national/feeder/default.rss"),
    ("ABC Australia", None, "ASIA-PAC", "https://www.abc.net.au/news/feed/51120/rss.xml"),
    ("Africanews", None, "AFRICA", "https://www.africanews.com/feed/rss"),
    ("Times of Israel", None, "MIDEAST", "https://www.timesofisrael.com/feed/"),
    ("MercoPress", None, "AMERICAS", "https://en.mercopress.com/rss/"),
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
                "diplomat diplomatic summit treaty mp mps referendum coalition cabinet impeachment opposition "
                "gop senator senators governor mayor",
    "finance": "market markets stocks stock shares economy economic inflation bank banks rates interest oil "
               "prices trade tariff tariffs earnings profit profits gdp investor investors dollar currency bond "
               "bonds jobs unemployment merger ipo crypto bitcoin revenue recession wall_street "
               "oilfield oil_field gas_field crude opec barrel barrels refinery pipeline fuel petrol drilling energy_prices lng",
    "tech": "ai tech technology software app apps apple google microsoft meta amazon nvidia chip chips "
            "semiconductor cyber cyberattack hack hackers hacked robot robots startup internet smartphone "
            "openai anthropic quantum social media tiktok online data breach",
    "science": "science study research scientists space nasa climate health disease virus vaccine cancer "
               "species planet fossil ocean medical hospital drug brain emissions weather earthquake "
               "telescope astronomers outbreak researchers wildlife biodiversity conservation",
}

# Headline words strong enough to override a feed's own topic (an oil-field story in a science feed is finance).
STRONG_WORDS = {
    "finance": "oilfield oil_field gas_field crude opec barrel stocks shares markets inflation interest_rate "
               "interest_rates earnings tariff tariffs economy recession",
    "politics": "election elections parliament senate congress minister president democrats republicans gop "
                "campaign lawmakers referendum coalition",
    "tech": "ai artificial_intelligence chip chips software app cyber cyberattack hack hackers smartphone startup "
            "tiktok openai",
    "science": "study scientists researchers nasa telescope species climate vaccine disease outbreak fossil",
}

# Places: (display name, region, longitude, keywords). A keyword starting with "=" is matched
# case-sensitively against the original text ("US" the country, not "us" the word);
# an underscore joins a multi-word phrase.
PLACES = [
    # Americas
    ("United States", "AMERICAS", -98, "=US =U.S. =USA america american americans united_states washington white_house wall_street =Fed new_york california texas florida pentagon democrat democrats republican republicans =GOP trump biden capitol_hill thanksgiving"),
    ("Canada", "AMERICAS", -106, "canada canadian canadians ottawa toronto quebec"),
    ("Greenland", "AMERICAS", -42, "greenland"),
    ("Mexico", "AMERICAS", -102, "mexico mexican mexicans"),
    ("Guatemala", "AMERICAS", -90, "guatemala guatemalan"),
    ("Honduras", "AMERICAS", -87, "honduras honduran"),
    ("El Salvador", "AMERICAS", -89, "el_salvador salvadoran"),
    ("Nicaragua", "AMERICAS", -85, "nicaragua nicaraguan"),
    ("Costa Rica", "AMERICAS", -84, "costa_rica"),
    ("Panama", "AMERICAS", -80, "panama panamanian"),
    ("Cuba", "AMERICAS", -79, "cuba cuban havana"),
    ("Haiti", "AMERICAS", -72, "haiti haitian"),
    ("Dominican Republic", "AMERICAS", -70, "dominican_republic"),
    ("Jamaica", "AMERICAS", -77, "jamaica jamaican"),
    ("Puerto Rico", "AMERICAS", -66, "puerto_rico"),
    ("Bahamas", "AMERICAS", -77, "bahamas bahamian"),
    ("Barbados", "AMERICAS", -59, "barbados"),
    ("Trinidad and Tobago", "AMERICAS", -61, "trinidad"),
    ("Belize", "AMERICAS", -88, "belize"),
    ("Guyana", "AMERICAS", -59, "guyana guyanese"),
    ("Suriname", "AMERICAS", -56, "suriname"),
    ("Colombia", "AMERICAS", -74, "colombia colombian bogota"),
    ("Venezuela", "AMERICAS", -66, "venezuela venezuelan caracas maduro"),
    ("Ecuador", "AMERICAS", -78, "ecuador ecuadorian"),
    ("Peru", "AMERICAS", -75, "peru peruvian lima"),
    ("Bolivia", "AMERICAS", -64, "bolivia bolivian"),
    ("Brazil", "AMERICAS", -51, "brazil brazilian brasilia lula"),
    ("Paraguay", "AMERICAS", -58, "paraguay"),
    ("Uruguay", "AMERICAS", -56, "uruguay"),
    ("Argentina", "AMERICAS", -64, "argentina argentine buenos_aires milei"),
    ("Chile", "AMERICAS", -71, "chile chilean santiago"),
    # Europe
    ("United Kingdom", "EUROPE", -2, "=UK britain british england english scotland scottish wales welsh northern_ireland london downing westminster bank_of_england north_sea shetland starmer"),
    ("Ireland", "EUROPE", -8, "ireland irish dublin"),
    ("Iceland", "EUROPE", -19, "iceland icelandic"),
    ("Portugal", "EUROPE", -8, "portugal portuguese lisbon"),
    ("Spain", "EUROPE", -4, "spain spanish madrid barcelona catalonia"),
    ("France", "EUROPE", 2, "france french paris macron"),
    ("Belgium", "EUROPE", 4, "belgium belgian"),
    ("Netherlands", "EUROPE", 5, "netherlands dutch amsterdam the_hague"),
    ("Luxembourg", "EUROPE", 6, "luxembourg"),
    ("Switzerland", "EUROPE", 8, "switzerland swiss geneva zurich"),
    ("Germany", "EUROPE", 10, "germany german berlin bundestag"),
    ("Denmark", "EUROPE", 10, "denmark danish copenhagen"),
    ("Norway", "EUROPE", 10, "norway norwegian oslo"),
    ("Italy", "EUROPE", 12, "italy italian rome milan meloni"),
    ("Austria", "EUROPE", 14, "austria austrian vienna"),
    ("Malta", "EUROPE", 14, "malta maltese"),
    ("Czech Republic", "EUROPE", 15, "czech czechia prague"),
    ("Sweden", "EUROPE", 15, "sweden swedish stockholm"),
    ("Slovenia", "EUROPE", 15, "slovenia slovenian"),
    ("Croatia", "EUROPE", 16, "croatia croatian"),
    ("Bosnia", "EUROPE", 18, "bosnia bosnian sarajevo"),
    ("Slovakia", "EUROPE", 19, "slovakia slovak"),
    ("Hungary", "EUROPE", 19, "hungary hungarian budapest orban"),
    ("Poland", "EUROPE", 19, "poland polish warsaw"),
    ("Montenegro", "EUROPE", 19, "montenegro"),
    ("Albania", "EUROPE", 20, "albania albanian"),
    ("Serbia", "EUROPE", 21, "serbia serbian belgrade"),
    ("Kosovo", "EUROPE", 21, "kosovo"),
    ("North Macedonia", "EUROPE", 22, "north_macedonia macedonian"),
    ("Greece", "EUROPE", 22, "greece greek athens"),
    ("Bulgaria", "EUROPE", 25, "bulgaria bulgarian"),
    ("Romania", "EUROPE", 25, "romania romanian bucharest"),
    ("Finland", "EUROPE", 26, "finland finnish helsinki"),
    ("Estonia", "EUROPE", 25, "estonia estonian"),
    ("Latvia", "EUROPE", 25, "latvia latvian"),
    ("Lithuania", "EUROPE", 24, "lithuania lithuanian"),
    ("Moldova", "EUROPE", 29, "moldova moldovan"),
    ("Belarus", "EUROPE", 28, "belarus belarusian minsk lukashenko"),
    ("Ukraine", "EUROPE", 31, "ukraine ukrainian ukrainians kyiv zelensky"),
    ("Russia", "EUROPE", 37, "russia russian russians kremlin moscow putin"),
    ("Cyprus", "EUROPE", 33, "cyprus cypriot"),
    ("Armenia", "EUROPE", 45, "armenia armenian yerevan"),
    ("Azerbaijan", "EUROPE", 48, "azerbaijan azerbaijani azeri baku"),
    ("Monaco", "EUROPE", 7, "monaco"),
    ("Vatican", "EUROPE", 12, "vatican holy_see"),
    ("EU", "EUROPE", 4, "=EU european_union european_commission brussels =ECB =NATO nato"),
    # Middle East
    ("Turkey", "MIDEAST", 35, "turkey turkish ankara istanbul erdogan"),
    ("Israel", "MIDEAST", 35, "israel israeli israelis netanyahu jerusalem tel_aviv"),
    ("Gaza", "MIDEAST", 34, "gaza palestinian palestinians hamas west_bank"),
    ("Lebanon", "MIDEAST", 36, "lebanon lebanese hezbollah beirut"),
    ("Syria", "MIDEAST", 38, "syria syrian damascus"),
    ("Jordan", "MIDEAST", 36, "jordanian amman"),
    ("Iraq", "MIDEAST", 44, "iraq iraqi baghdad"),
    ("Iran", "MIDEAST", 53, "iran iranian tehran khamenei"),
    ("Saudi Arabia", "MIDEAST", 45, "saudi saudis riyadh"),
    ("Yemen", "MIDEAST", 48, "yemen yemeni houthi houthis"),
    ("UAE", "MIDEAST", 54, "=UAE emirates emirati dubai abu_dhabi"),
    ("Qatar", "MIDEAST", 51, "qatar qatari doha"),
    ("Kuwait", "MIDEAST", 48, "kuwait kuwaiti"),
    ("Bahrain", "MIDEAST", 50, "bahrain"),
    ("Oman", "MIDEAST", 57, "oman omani"),
    # Africa
    ("Morocco", "AFRICA", -7, "morocco moroccan rabat"),
    ("Algeria", "AFRICA", 3, "algeria algerian"),
    ("Tunisia", "AFRICA", 9, "tunisia tunisian"),
    ("Libya", "AFRICA", 17, "libya libyan tripoli"),
    ("Egypt", "AFRICA", 31, "egypt egyptian cairo"),
    ("Sudan", "AFRICA", 30, "sudan sudanese khartoum darfur"),
    ("South Sudan", "AFRICA", 31, "south_sudan"),
    ("Ethiopia", "AFRICA", 39, "ethiopia ethiopian addis_ababa tigray"),
    ("Eritrea", "AFRICA", 39, "eritrea eritrean"),
    ("Somalia", "AFRICA", 46, "somalia somali mogadishu"),
    ("Kenya", "AFRICA", 38, "kenya kenyan nairobi"),
    ("Uganda", "AFRICA", 32, "uganda ugandan kampala"),
    ("Tanzania", "AFRICA", 35, "tanzania tanzanian"),
    ("Rwanda", "AFRICA", 30, "rwanda rwandan kigali"),
    ("Burundi", "AFRICA", 30, "burundi"),
    ("DR Congo", "AFRICA", 23, "congo congolese kinshasa goma"),
    ("Nigeria", "AFRICA", 8, "nigeria nigerian lagos abuja"),
    ("Ghana", "AFRICA", -1, "ghana ghanaian accra"),
    ("Senegal", "AFRICA", -14, "senegal senegalese dakar"),
    ("Mali", "AFRICA", -4, "mali malian bamako"),
    ("Burkina Faso", "AFRICA", -2, "burkina_faso"),
    ("Niger", "AFRICA", 8, "niger niamey"),
    ("Chad", "AFRICA", 18, "chadian n'djamena"),
    ("Cameroon", "AFRICA", 12, "cameroon cameroonian"),
    ("Ivory Coast", "AFRICA", -5, "ivory_coast ivorian"),
    ("Sierra Leone", "AFRICA", -12, "sierra_leone"),
    ("Liberia", "AFRICA", -9, "liberia liberian"),
    ("Mauritania", "AFRICA", -10, "mauritania mauritanian"),
    ("Gambia", "AFRICA", -15, "gambia gambian"),
    ("Benin", "AFRICA", 2, "benin"),
    ("Togo", "AFRICA", 1, "togo togolese"),
    ("Gabon", "AFRICA", 11, "gabon gabonese"),
    ("Central African Republic", "AFRICA", 21, "central_african_republic"),
    ("Djibouti", "AFRICA", 43, "djibouti"),
    ("Lesotho", "AFRICA", 28, "lesotho"),
    ("Eswatini", "AFRICA", 31, "eswatini swaziland"),
    ("Mauritius", "AFRICA", 57, "mauritius"),
    ("Angola", "AFRICA", 18, "angola angolan"),
    ("Zambia", "AFRICA", 28, "zambia zambian"),
    ("Zimbabwe", "AFRICA", 30, "zimbabwe zimbabwean harare"),
    ("Mozambique", "AFRICA", 35, "mozambique mozambican"),
    ("Malawi", "AFRICA", 34, "malawi malawian"),
    ("Madagascar", "AFRICA", 47, "madagascar"),
    ("Namibia", "AFRICA", 18, "namibia namibian"),
    ("Botswana", "AFRICA", 24, "botswana"),
    ("South Africa", "AFRICA", 25, "south_africa south_african johannesburg pretoria cape_town"),
    ("Africa", "AFRICA", 20, "africa african sahel"),
    # Asia-Pacific
    ("Kazakhstan", "ASIA-PAC", 67, "kazakhstan"),
    ("Uzbekistan", "ASIA-PAC", 64, "uzbekistan"),
    ("Kyrgyzstan", "ASIA-PAC", 75, "kyrgyzstan kyrgyz"),
    ("Tajikistan", "ASIA-PAC", 71, "tajikistan tajik"),
    ("Turkmenistan", "ASIA-PAC", 59, "turkmenistan"),
    ("Afghanistan", "ASIA-PAC", 67, "afghanistan afghan afghans kabul taliban"),
    ("Pakistan", "ASIA-PAC", 69, "pakistan pakistani islamabad karachi lahore"),
    ("India", "ASIA-PAC", 79, "india indian indians delhi mumbai modi"),
    ("Sri Lanka", "ASIA-PAC", 81, "sri_lanka sri_lankan colombo"),
    ("Nepal", "ASIA-PAC", 84, "nepal nepali kathmandu"),
    ("Bhutan", "ASIA-PAC", 90, "bhutan"),
    ("Maldives", "ASIA-PAC", 73, "maldives"),
    ("Bangladesh", "ASIA-PAC", 90, "bangladesh bangladeshi dhaka"),
    ("Myanmar", "ASIA-PAC", 96, "myanmar burma burmese yangon"),
    ("Thailand", "ASIA-PAC", 101, "thailand thai bangkok"),
    ("Cambodia", "ASIA-PAC", 105, "cambodia cambodian phnom_penh"),
    ("Laos", "ASIA-PAC", 103, "laos"),
    ("Vietnam", "ASIA-PAC", 106, "vietnam vietnamese hanoi"),
    ("Malaysia", "ASIA-PAC", 102, "malaysia malaysian kuala_lumpur"),
    ("Singapore", "ASIA-PAC", 104, "singapore singaporean"),
    ("Indonesia", "ASIA-PAC", 113, "indonesia indonesian jakarta bali"),
    ("Philippines", "ASIA-PAC", 122, "philippines filipino manila"),
    ("Brunei", "ASIA-PAC", 115, "brunei"),
    ("East Timor", "ASIA-PAC", 126, "east_timor timor-leste"),
    ("China", "ASIA-PAC", 104, "china chinese beijing shanghai xi_jinping"),
    ("Hong Kong", "ASIA-PAC", 114, "hong_kong"),
    ("Taiwan", "ASIA-PAC", 121, "taiwan taiwanese taipei"),
    ("Mongolia", "ASIA-PAC", 105, "mongolia mongolian"),
    ("North Korea", "ASIA-PAC", 127, "north_korea north_korean pyongyang kim_jong_un"),
    ("South Korea", "ASIA-PAC", 128, "south_korea south_korean seoul"),
    ("Japan", "ASIA-PAC", 138, "japan japanese tokyo"),
    ("Australia", "ASIA-PAC", 134, "australia australian australians sydney melbourne canberra"),
    ("New Zealand", "ASIA-PAC", 174, "new_zealand wellington auckland"),
    ("Papua New Guinea", "ASIA-PAC", 147, "papua_new_guinea"),
    ("Pacific Islands", "ASIA-PAC", 178, "fiji samoa tonga vanuatu solomon_islands pacific_islands"),
]


def _words(text):
    """Keywords are space-separated; an underscore joins a phrase (white_house)."""
    return [w.replace("_", " ") for w in text.split()]


def _rx(word):
    if word.startswith("="):
        return ("cs", re.compile(r"(?<![A-Za-z])" + re.escape(word[1:]) + r"(?![A-Za-z])"))
    return ("ci", re.compile(r"(?<![a-z])" + re.escape(word.lower()) + r"s?(?![a-z])"))   # plurals count too


CATEGORY_RX = {k: [_rx(w) for w in _words(v)] for k, v in CATEGORY_WORDS.items()}
STRONG_RX = {k: [_rx(w) for w in _words(v)] for k, v in STRONG_WORDS.items()}
BROAD_PLACES = {"Africa", "EU", "Pacific Islands"}
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


def _low(text):
    return " " + (text or "").lower() + " "


def classify_cat(title, summary, fixed=None):
    """A feed's own topic wins unless the headline clearly belongs to one other desk."""
    tl, sl = _low(title), _low(summary)
    scores = {k: 2 * _count(p, title, tl) + _count(p, summary or "", sl) for k, p in CATEGORY_RX.items()}
    if fixed:
        strong = [k for k, p in STRONG_RX.items() if _count(p, title, tl)]
        others = [k for k in strong if k != fixed]
        if fixed not in strong and len(others) == 1 and scores[others[0]] >= scores.get(fixed, 0) + 2:
            return others[0]
        return fixed
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "world"


def _place_hits(text):
    """{place index: (mentions, first position)} for every place named in the text. Where names overlap,
    the longer one wins, so "South Sudan" is not also Sudan and "South Africa" is not also Africa."""
    if not text:
        return {}
    low = text.lower()
    spans = []
    for i, (_, _, _, pats) in enumerate(PLACE_RX):
        for kind, rx in pats:
            for m in rx.finditer(text if kind == "cs" else low):
                spans.append((m.start(), m.end(), i))
    spans.sort(key=lambda s: (s[0] - s[1], s[0]))
    taken, hits = [], {}
    for a, b, i in spans:
        if any(a < y and x < b for x, y in taken):
            continue
        taken.append((a, b))
        n, at = hits.get(i, (0, 10**9))
        hits[i] = (n + 1, min(at, a))
    return hits


def locate(title, summary, default_region=None):
    """Return (region, place, longitude).

    The headline decides first: the first place it names wins, preferring places in the feed's own
    region (US politics, BBC Africa). With nothing in the headline, the summary sets the region, but a
    country is named only when one clearly leads; otherwise the story gets just its region."""
    home = default_region if default_region and default_region != "GLOBAL" else None

    def pick(i):
        name, region, lon, _ = PLACE_RX[i]
        return (region, None, None) if name in BROAD_PLACES else (region, name, lon)

    head = _place_hits(title)
    if head:
        order = sorted(head, key=lambda i: (PLACE_RX[i][1] != home, head[i][1]))
        return pick(order[0])

    body = _place_hits(summary)
    if not body:
        return home or "GLOBAL", None, None
    weight = {}
    for i, (n, _) in body.items():
        weight[PLACE_RX[i][1]] = weight.get(PLACE_RX[i][1], 0) + n
    if home:
        weight[home] = weight.get(home, 0) + 1.5
    region = max(weight, key=weight.get)
    if sorted(weight.values())[-2:-1] == [weight[region]]:
        return home or "GLOBAL", None, None          # two regions tie: too unclear to place
    inside = sorted((n, -at, i) for i, (n, at) in body.items()
                    if PLACE_RX[i][1] == region and PLACE_RX[i][0] not in BROAD_PLACES)
    if not inside:
        return region, None, None
    n, _, i = inside[-1]
    runner_up = inside[-2][0] if len(inside) > 1 else 0
    sure = n > runner_up and (n >= 2 or (runner_up == 0 and region == home))
    return pick(i) if sure else (region, None, None)


OUTLET_PREFIXES = ("BBC", "Guardian", "NPR")


def outlet(source):
    """BBC World and BBC Asia are one outlet, not two."""
    return next((p for p in OUTLET_PREFIXES if source.startswith(p)), source)


# ---------- matching headlines from different outlets ----------

WORD_RX = re.compile(r"[^\W\d_](?:[^\W\d_]|['’\-])*[^\W\d_]|[^\W\d_]|\d+")
GENERIC = set("live update updates latest video watch report reports week year day analysis explainer".split())
SAME_STORY_HOURS = 18           # headlines further apart than this are different stories
COMMON_NAME = 0.03              # a name in more than this share of headlines (Trump) links nothing on its own


def _fold(w):
    """Vučić and Vucic are the same word."""
    return "".join(c for c in unicodedata.normalize("NFKD", w) if not unicodedata.combining(c))


def _stem(w):
    """Light stemming so "votes", "voted" and "vote" match."""
    for suffix in ("ing", "ed", "es", "s"):
        if w.endswith(suffix) and len(w) - len(suffix) >= 4:
            w = w[: -len(suffix)]
            break
    return w[:-1] if w.endswith("e") and len(w) > 4 else w


def tokens(title):
    out = set()
    for w in WORD_RX.findall(title or ""):
        for part in _fold(w.lower()).replace("’", "'").split("-"):
            part = part.strip("'")
            if part.endswith("'s"):
                part = part[:-2]
            if len(part) > 2 and part not in STOP and part not in GENERIC:
                out.add(_stem(part))
    return out


def _title_case(ws):
    """'Senate Passes Stopgap Bill To Avert Shutdown': small common words are capitalised too."""
    common = [w for w in ws[1:] if w.lower() in STOP]
    if common:
        return sum(w[0].isupper() for w in common) > len(common) / 2
    return sum(w[0].isupper() for w in ws[1:]) > 0.7 * (len(ws) - 1)


# Capitalised words that are titles, institutions or dates rather than the name of one story, and place
# words (places are compared separately). Leaders and groups GLOBEWIRE uses to place stories are names.
TITLE_WORDS = tokens("""President Prime Minister Ministers King Queen Pope Chancellor Senator Governor Mayor General
Secretary House Senate Congress Parliament Court Supreme Police Army Government Party Labour Republicans Democrats
Monday Tuesday Wednesday Thursday Friday Saturday Sunday January February March April May June July August
September October November December AI""")
PLACE_TERMS = set()
for _name, _region, _lon, _words in PLACES:
    PLACE_TERMS |= tokens(_name)
    for _w in _words.split():
        PLACE_TERMS |= tokens(_w.lstrip("=").replace("_", " "))
PLACE_TERMS -= tokens("""trump biden maduro lula milei starmer macron meloni orban lukashenko zelensky putin netanyahu
erdogan khamenei modi xi jinping kim jong hamas hezbollah taliban houthi houthis""")


def name_terms(titles):
    """Terms written with a capital letter mid-headline in sentence-case headlines (Lula, Boelo, Starlink).
    Judged across all headlines, so a name also counts in Title Case headlines that mention it."""
    cap, seen = Counter(), Counter()
    for title in titles:
        ws = WORD_RX.findall(title or "")
        if len(ws) < 4 or _title_case(ws):
            continue
        for w in ws[1:]:
            for term in tokens(w):
                seen[term] += 1
                cap[term] += w[0].isupper()
    return {term for term, n in seen.items() if cap[term] >= 0.6 * n} - PLACE_TERMS - TITLE_WORDS


def same_story(a, b):
    """Do two headlines report the same story? Different outlets word a story differently ("Lula wins
    Brazil runoff" / "Brazil's Lula seals re-election"), so besides sharing enough words, two headlines
    from different outlets in the same place match when they share uncommon names and some wording."""
    if abs(a["ts"] - b["ts"]) > SAME_STORY_HOURS * 3600 * 1000:
        return None
    shared = a["tok"] & b["tok"]
    if len(shared) < 2:
        return None
    jac = len(shared) / len(a["tok"] | b["tok"])
    if len(shared) >= 3 and jac >= 0.30:
        return True
    if outlet(a["source"]) == outlet(b["source"]):
        return False  # a second article from the same outlet is a separate story unless the wording matches
    if a["place"] and b["place"] and a["place"] != b["place"]:
        return False
    named = len(shared & a["names"])
    return (named >= 2 and jac >= 0.15) or (named == 1 and len(shared) >= 3 and jac >= 0.18)


def cluster(items):
    """Group headlines that report the same story. Every pair that matches is linked, and linked headlines
    form one story, so the result doesn't depend on the order headlines arrive. Only pairs sharing a word
    are compared."""
    names = name_terms([x["title"] for x in items])
    df = Counter(term for x in items for term in x["tok"])
    names = {term for term in names if df[term] <= max(3, COMMON_NAME * len(items))}
    for x in items:
        x["names"] = x["tok"] & names
    parent = list(range(len(items)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    index = {}
    for i, x in enumerate(items):
        for j in {j for term in x["tok"] for j in index.get(term, ())}:
            if root(i) != root(j) and same_story(x, items[j]):
                parent[root(i)] = root(j)
        for term in x["tok"]:
            index.setdefault(term, []).append(i)
    groups = {}
    for i, x in enumerate(items):
        groups.setdefault(root(i), []).append(x)
    return [{"items": sorted(g, key=lambda x: -x["ts"]), "outlets": {outlet(x["source"]) for x in g}} for g in groups.values()]


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


def build(previous=None, max_age_hours=36, per_feed=50, workers=16):
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
                region, place, lon = locate(it["title"], it["summary"], default_region)
                raw_items.append({
                    **it,
                    "ts": min(now_ms, int(d.timestamp() * 1000)),
                    "source": name,
                    "cat": classify_cat(it["title"], it["summary"], fixed_cat),
                    "fixed": bool(fixed_cat),
                    "region": region, "place": place, "lon": lon,
                    "tok": tokens(it["title"]),
                })
                kept += 1
            sources.append({"name": name, "cat": fixed_cat or "auto", "ok": err is None, "count": kept, "error": err})

    # merge stories several outlets carry
    clusters = cluster(raw_items)

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
            if outlet(x["source"]) not in seen:
                seen.add(outlet(x["source"]))
                per_source.append({"source": x["source"], "title": x["title"], "url": x["url"], "ts": x["ts"]})
        votes = {}
        for x in items:
            votes[x["cat"]] = votes.get(x["cat"], 0) + (1.2 if x["fixed"] else 1.0)
        located = [x for x in items if x["region"] != "GLOBAL"]
        regions = [x["region"] for x in located] or ["GLOBAL"]
        region = max(set(regions), key=lambda r: (regions.count(r), r == lead["region"]))
        placed = [x for x in located if x["region"] == region and x["place"]]
        place_names = [x["place"] for x in placed]
        counts = sorted(((place_names.count(n), n) for n in set(place_names)), reverse=True)
        place = counts[0][1] if counts and (len(counts) == 1 or counts[0][0] > counts[1][0]) else None
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
            "cat": max(votes, key=votes.get),
            "region": region, "place": place, "lon": lon,
            "ts": max(x["ts"] for x in items),
        }
        prev = prev_by_id.get(story["id"]) or next((prev_by_url[p["url"]] for p in per_source if p["url"] in prev_by_url), None)
        story["first_seen"] = (prev or {}).get("first_seen") or now_ms
        story["prev_signal"] = (prev or {}).get("signal") or 0
        jumped = have_baseline and (story["signal"] - story["prev_signal"] >= 2 or (prev is None and story["signal"] >= 3))
        since, base = (prev or {}).get("breaking_since"), (prev or {}).get("breaking_from")
        if jumped and not since:
            since, base = now_ms, story["prev_signal"]
        if since and (now_ms - since > BREAKING_WINDOW_MS or story["signal"] <= (base or 0)):
            since, base = None, None
        story["breaking_since"] = since
        story["breaking_from"] = base
        story["breaking"] = since is not None
        stories.append(story)

    def score(s):
        # coverage matters, but a story's weight halves every 10 hours so today's news leads
        age_h = max(0, (now_ms - s["ts"]) / 3.6e6)
        return (s["signal"] * 3 + (4 if s["breaking"] else 0)) * 0.5 ** (age_h / 10) + 1.5 * max(0, 1 - age_h / 6)

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
        flag = " \033[41;97m SPREADING FAST \033[0m" if s.get("breaking") else ""
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


def sources_report(data):
    """Where this update's news came from, shown as notices on each GitHub Actions run: each feed's
    count (or its error), stories by region and desk, and how many stories more than one outlet carries."""
    src, stories = data["sources"], data["stories"]
    feeds = "\n".join(f"{s['name']}: {s.get('count', '?') if s['ok'] else 'FAILED ' + (s.get('error') or '')[:80]}"
                      for s in sorted(src, key=lambda s: (s["ok"], -s.get("count", 0))))
    outlets = Counter(outlet(s["name"]) for s in src if s["ok"])
    by = lambda key: ", ".join(f"{k} {n}" for k, n in Counter(x[key] for x in stories).most_common())
    multi = Counter(min(x["signal"], 4) for x in stories)
    several = ", ".join(f"{o} {n}" for o, n in outlets.most_common() if n > 1)
    mix = (f"{len(stories)} stories from {sum(1 for s in src if s['ok'])} of {len(src)} feeds and {len(outlets)} outlets"
           f"{f' ({several} feeds each)' if several else ''}.\n"
           f"By region: {by('region')}.\nBy desk: {by('cat')}.\n"
           f"Carried by 1 outlet: {multi[1]}, 2: {multi[2]}, 3: {multi[3]}, 4 or more: {multi[4]}.")
    return [("GLOBEWIRE sources", mix), ("GLOBEWIRE feeds", feeds)]


def build_site(out_dir, previous):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    data = build(previous)
    (out / "news.json").write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    (out / "index.html").write_bytes(page_html())
    ok = sum(1 for s in data["sources"] if s["ok"])
    hot = sum(1 for s in data["stories"] if s["breaking"])
    print(f"Site built in {out}: {ok}/{len(data['sources'])} feeds on air, {len(data['stories'])} stories, {hot} breaking.")
    if os.environ.get("GITHUB_ACTIONS") == "true":
        for title, text in sources_report(data):
            esc = text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
            print(f"::notice title={title}::{esc}")
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
