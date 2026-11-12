"""Daily English News, step 1: find today's stories in RSS feeds.

Only what a feed publishes is used: the headline, the short summary, the link and the publication time. Article
pages are not downloaded. The one exception is NASA (a US government agency: its news releases are generally not
subject to copyright), whose feed carries the full release text.

Company blogs (Google, OpenAI) are a company's own announcement, and some feeds carry only a one-line teaser: those
stories are never turned into a lesson on their own. They are used to see whether another outlet covers the same
story ("corroborated"), which is shown next to the lesson.

Nothing here calls Gemini; the stories are stored and step 2 (news_lessons) picks from them.
"""
from __future__ import annotations

import datetime as dt
import email.utils
import hashlib
import html
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

USER_AGENT = "LISEN/1.0 (personal English listening practice; RSS reader)"
TIMEOUT_S = 20
MAX_BYTES = 5_000_000
MAX_AGE_DAYS = 3          # stories older than this are not offered as "today's news"

CATEGORIES = {"technology": "AI・テクノロジー", "business": "ビジネス・経済", "world": "世界", "science": "科学"}
KIND_LABEL = {"press": "報道機関", "government": "政府機関の発表", "company": "企業自身の発表"}


@dataclass(frozen=True)
class Feed:
    id: str
    url: str
    publisher: str
    category: str
    kind: str                 # press / government / company
    lesson: bool = True       # summaries are long enough to build a lesson from
    full_text: bool = False   # the feed's full text may be used (public domain)


FEEDS: tuple[Feed, ...] = (
    Feed("guardian-tech", "https://www.theguardian.com/uk/technology/rss", "The Guardian", "technology", "press"),
    Feed("guardian-business", "https://www.theguardian.com/business/rss", "The Guardian", "business", "press"),
    Feed("guardian-world", "https://www.theguardian.com/world/rss", "The Guardian", "world", "press"),
    Feed("guardian-science", "https://www.theguardian.com/science/rss", "The Guardian", "science", "press"),
    Feed("nasa", "https://www.nasa.gov/news-release/feed/", "NASA", "science", "government", full_text=True),
    Feed("bbc-tech", "https://feeds.bbci.co.uk/news/technology/rss.xml", "BBC News", "technology", "press", lesson=False),
    Feed("google-ai", "https://blog.google/technology/ai/rss/", "Google", "technology", "company", lesson=False),
    Feed("openai", "https://openai.com/news/rss.xml", "OpenAI", "technology", "company", lesson=False),
)
FEED_BY_ID = {f.id: f for f in FEEDS}

NS = {"content": "http://purl.org/rss/1.0/modules/content/", "dc": "http://purl.org/dc/elements/1.1/",
      "atom": "http://www.w3.org/2005/Atom"}
# live blogs change all day and mix many stories; not a single news item to learn from
LIVE = re.compile(r"(\bas it happened\b|\blive\b\s*$|[–-]\s*(follow\s+)?live\b|\blive updates?\b|/live/)", re.I)
TRACKING = re.compile(r"^(utm_|cmp$|CMP$|fbclid$|gclid$)")


class FeedError(Exception):
    pass


@dataclass
class Entry:
    feed: str
    url: str
    title: str
    summary: str
    published: str | None          # ISO 8601, UTC; None when the feed gives no date
    content: str = ""              # full text, only for feeds whose text may be used
    id: str = field(default="")

    def __post_init__(self):
        if not self.id:
            self.id = entry_id(self.url)


def entry_id(url: str) -> str:
    return hashlib.sha256(canonical_url(url).encode()).hexdigest()[:16]


def canonical_url(url: str) -> str:
    """The same story reached through different links (tracking parameters, http/https, trailing slash)."""
    u = urllib.parse.urlsplit(url.strip())
    query = urllib.parse.urlencode([(k, v) for k, v in urllib.parse.parse_qsl(u.query) if not TRACKING.match(k)])
    path = u.path.rstrip("/") or "/"
    return urllib.parse.urlunsplit(("https", u.netloc.lower(), path, query, ""))


def clean_text(s: str | None) -> str:
    """HTML to plain text: tags removed, entities decoded, whitespace collapsed."""
    if not s:
        return ""
    s = re.sub(r"<(script|style)\b.*?</\1>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<br\s*/?>|</p>|</li>|</h\d>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t ]+", " ", s)
    s = re.sub(r"\s*\n\s*", "\n", s)
    # feed boilerplate, not part of the story
    s = "\n".join(l for l in s.split("\n") if not BOILERPLATE.match(l))
    return s.strip()


BOILERPLATE = re.compile(r"^(continue reading\.*|sign up (for|to) .*|get our .*(newsletter|email|app|podcast).*|read more.*)$", re.I)


def parse_date(s: str | None) -> str | None:
    """RFC 822 (RSS) or ISO 8601 (Atom / dc:date) to UTC ISO 8601. A date without a time zone is taken as UTC."""
    if not s:
        return None
    s = s.strip()
    d = None
    try:
        d = email.utils.parsedate_to_datetime(s)
    except (TypeError, ValueError, IndexError):
        try:
            d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d.astimezone(dt.timezone.utc).isoformat(timespec="seconds")


def parse_feed(data: bytes, feed: Feed) -> list[Entry]:
    """RSS 2.0 or Atom. Documents that declare entities are refused (no entity expansion of untrusted XML)."""
    if b"<!ENTITY" in data[:20000] or b"<!DOCTYPE" in data[:20000]:
        raise FeedError(f"{feed.id}: feed declares a DOCTYPE / entities; refused")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        raise FeedError(f"{feed.id}: not valid XML ({e})") from e
    out: list[Entry] = []
    items = root.iter("item")
    atom = root.tag == f"{{{NS['atom']}}}feed"
    if atom:
        items = root.iter(f"{{{NS['atom']}}}entry")
    for it in items:
        if atom:
            title = it.findtext("atom:title", "", NS)
            link_el = it.find("atom:link[@rel='alternate']", NS) or it.find("atom:link", NS)
            url = link_el.get("href", "") if link_el is not None else ""
            summary = it.findtext("atom:summary", "", NS)
            content = it.findtext("atom:content", "", NS)
            published = it.findtext("atom:published", None, NS) or it.findtext("atom:updated", None, NS)
        else:
            title = it.findtext("title", "")
            url = it.findtext("link", "") or it.findtext("guid", "")
            summary = it.findtext("description", "")
            content = it.findtext("content:encoded", "", NS)
            published = it.findtext("pubDate") or it.findtext("dc:date", None, NS)
        title = clean_text(title)
        if not title or not url.startswith(("http://", "https://")):
            continue
        if LIVE.search(title) or LIVE.search(urllib.parse.urlsplit(url).path):
            continue
        out.append(Entry(feed=feed.id, url=canonical_url(url), title=title, summary=clean_text(summary),
                         published=parse_date(published), content=clean_text(content) if feed.full_text else ""))
    return out


def fetch_feed(feed: Feed, opener=None) -> list[Entry]:
    req = urllib.request.Request(feed.url, headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/xml"})
    try:
        with (opener or urllib.request.urlopen)(req, timeout=TIMEOUT_S) as r:
            data = r.read(MAX_BYTES + 1)
    except OSError as e:
        raise FeedError(f"{feed.id}: {e}") from e
    if len(data) > MAX_BYTES:
        raise FeedError(f"{feed.id}: feed larger than {MAX_BYTES} bytes")
    return parse_feed(data, feed)


# --- same story in several feeds -----------------------------------------------------------------

STOP = set("a an the of to in on for and or with as at by from is are was be been has have it its this that after over "
           "into about new says said will can how why what who you your we our more than up out not".split())


def title_words(title: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+(?:'[a-z]+)?", title.lower().replace("’", "'"))
    return {w.removesuffix("'s") for w in words if w not in STOP and len(w) > 2}


def same_story(a: str, b: str) -> bool:
    """Two headlines about one event: they share at least 3 content words and over 35% of them."""
    wa, wb = title_words(a), title_words(b)
    if not wa or not wb:
        return False
    common = wa & wb
    return len(common) >= 3 and len(common) / len(wa | wb) > 0.35


def fresh(entry: Entry, now: dt.datetime | None = None) -> bool:
    if not entry.published:
        return False
    now = now or dt.datetime.now(dt.timezone.utc)
    age = now - dt.datetime.fromisoformat(entry.published)
    return dt.timedelta(hours=-2) <= age <= dt.timedelta(days=MAX_AGE_DAYS)
