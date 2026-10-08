"""Search and page reading for research runs.

Every model gets the same web access because the app does it, not the model.

Reading a page tries, in order, until one gives real text:
  1. cache        same URL read in the last few days
  2. fetch        plain request, main text pulled out (PDFs too)
  3. webclaw      optional binary that looks like a real browser to the site
                  (gets past most bot blocks); used when fetch is blocked or thin
  4. browser      optional headless Chromium, for pages that need JavaScript
  5. blocked      recorded as "blocked, not read", never guessed at

Polite by default: one request at a time per site with a short gap, no logins,
no paywalls.
"""

from __future__ import annotations

import hashlib
import html as html_lib
import io
import json
import logging
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse, urlunparse, parse_qs, parse_qsl, urlencode

import httpx

from .safety import public_url

log = logging.getLogger(__name__)

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/128.0 Safari/537.36")
MIN_TEXT = 400          # fewer characters than this counts as "thin"
MAX_BYTES = 8_000_000

_BLOCK_SIGNS = re.compile(
    r"just a moment\.\.\.|checking your browser|cf-chl|enable javascript and cookies|"
    r"access denied|attention required|are you a robot|verify you are human|"
    r"please enable js|captcha", re.I)


@dataclass
class SearchHit:
    url: str
    title: str = ""
    snippet: str = ""
    engine: str = ""


@dataclass
class Page:
    url: str
    ok: bool
    text: str = ""
    title: str = ""
    published: str = ""
    retrieval: str = ""
    error: str = ""
    tried: tuple = ()


# ── URLs ─────────────────────────────────────────────────────────────
_TRACKING = re.compile(r"^(utm_|fbclid|gclid|mc_|ref$|ref_src|igshid)")


def clean_url(url: str) -> str:
    try:
        p = urlparse(url.strip())
    except ValueError:
        return url
    q = [(k, v) for k, v in parse_qsl(p.query) if not _TRACKING.match(k)]
    return urlunparse((p.scheme, p.netloc.lower(), p.path.rstrip("/") or "/", "",
                       urlencode(q), ""))


def readable_url(url: str) -> str:
    """Some sites serve a plain version that needs no JavaScript."""
    p = urlparse(url)
    if (p.hostname or "").endswith("reddit.com") and p.hostname != "old.reddit.com":
        return urlunparse(p._replace(netloc="old.reddit.com"))
    return url


def host(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


# ── Source type, a first guess the reader model can overrule ─────────
_FORUM = ("reddit.com", "news.ycombinator.com", "stackexchange.com", "stackoverflow.com",
          "quora.com", "forum", "community.", "discourse", "level1techs.com")
_LISTICLE = re.compile(r"(^|[/-])(best|top-\d+|top|vs|review|reviews|deals|coupon)([/-]|$)")


def guess_kind(url: str) -> str:
    h, path = host(url), urlparse(url).path.lower()
    if any(s in h for s in _FORUM) or "/forum" in path or "/threads/" in path:
        return "forum"
    if ("." + h).endswith((".gov", ".gov.uk", ".edu", ".ac.uk")) or ".gov." in h:
        return "primary"
    if h.startswith(("docs.", "developer.", "support.")) or h in ("github.com", "arxiv.org"):
        return "primary"
    return "secondary"


def rank_hits(hits: list[SearchHit], seen: set[str], per_host: int = 2) -> list[SearchHit]:
    """De-duplicate, skip pages already read, cap each site, push listicles down.

    Search engines rank SEO pages above original sources; this keeps a small
    reading budget from being spent on five copies of the same roundup.
    """
    out, counts, dupes = [], {}, set()
    for h in hits:
        u = clean_url(h.url)
        if not u.startswith("http") or u in dupes or u in seen:
            continue
        dupes.add(u)
        hn = host(u)
        if counts.get(hn, 0) >= per_host:
            continue
        counts[hn] = counts.get(hn, 0) + 1
        h.url = u
        out.append(h)
    demoted = [h for h in out if _LISTICLE.search(urlparse(h.url).path.lower())]
    return [h for h in out if h not in demoted] + demoted


# ── Search providers ─────────────────────────────────────────────────
_STOP = set("a an and are as at be by for from how in is it of on or that the to vs was what which who why with best per".split())


def key_terms(query: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9][a-z0-9.+-]*", query.lower()) if t not in _STOP and len(t) > 1]


def relevance(hit: SearchHit, terms: list[str]) -> float:
    """Share of the query's key words found in a result's title, snippet and address."""
    if not terms:
        return 1.0
    blob = f"{hit.title} {hit.snippet} {hit.url}".lower()
    return sum(1 for t in terms if t in blob) / len(terms)


def _ddg_target(link: str) -> str:
    link = html_lib.unescape(link)
    if "duckduckgo.com/l/?" in link:                  # redirect wrapper
        link = parse_qs(urlparse(link).query).get("uddg", [""])[0]
    if "duckduckgo.com/y.js" in link or not link.startswith("http"):
        return ""                                       # ads, internal links
    return link


def _parse_ddg_html(page: str, n: int) -> list[SearchHit]:
    out = []
    strip = lambda t: html_lib.unescape(re.sub(r"<[^>]+>", "", t or "")).strip()
    for block in page.split('result__body')[1:]:
        m = re.search(r'class="result__a" href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
        url = _ddg_target(m.group(1)) if m else ""
        if not url:
            continue
        sn = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', block, re.S)
        out.append(SearchHit(url, strip(m.group(2)), strip(sn.group(1) if sn else ""), "duckduckgo"))
        if len(out) >= n:
            break
    return out


def _parse_ddg_markdown(md: str, n: int) -> list[SearchHit]:
    out = []
    for part in re.split(r"\n(?=## \[)", md):
        m = re.match(r"## \[(.+?)\]\((\S+?)\)", part)
        url = _ddg_target(m.group(2)) if m else ""
        if not url:
            continue
        clean = lambda t: re.sub(r"\*\*", "", t).strip()
        title = clean(m.group(1))
        texts = [clean(t) for t in re.findall(r"\[([^\[\]]{60,})\]\(", part)]
        texts = [t for t in texts if t != title]
        out.append(SearchHit(url, title, texts[0] if texts else "", "duckduckgo"))
        if len(out) >= n:
            break
    return out


class Searcher:
    """SearXNG by default (free, no key), Brave or Tavily with a key.

    Self-hosted SearXNG often loses engines to rate limits and CAPTCHAs and
    then returns off-topic results from whatever engine is left. So results
    are scored against the query, and when too few are on topic the search
    is repeated on DuckDuckGo direct before giving up.
    """

    def __init__(self, cfg: dict):
        self.provider = (cfg.get("provider") or "searxng").lower()
        self.fallback = cfg.get("fallback", "duckduckgo")
        self.cfg = cfg
        self.count = 0
        self.errors = 0
        self.fallbacks = 0
        self.health: list[str] = []      # engine problems seen, shown in the report

    def search(self, query: str, n: int = 8) -> list[SearchHit]:
        self.count += 1
        terms = key_terms(query)
        hits = self._run(self.provider, query, n)
        good = [h for h in hits if relevance(h, terms) >= 0.5]
        if len(good) < 3 and self.fallback and self.fallback != self.provider:
            self.fallbacks += 1
            more = [h for h in self._run(self.fallback, query, n) if relevance(h, terms) >= 0.5]
            seen = {h.url for h in good}
            good += [h for h in more if h.url not in seen]
        return good

    def _run(self, provider: str, query: str, n: int) -> list[SearchHit]:
        fn = {"searxng": self._searxng, "brave": self._brave, "tavily": self._tavily,
              "duckduckgo": self._ddg}.get(provider, self._searxng)
        try:
            return fn(query, n)
        except Exception as e:  # network, bad key, bad JSON
            self.errors += 1
            self._note(f"{provider}: {e}")
            log.warning("search failed (%s): %s", provider, e)
            return []

    def _note(self, msg: str) -> None:
        if msg not in self.health and len(self.health) < 20:
            self.health.append(msg)

    def _searxng(self, q, n):
        base = (self.cfg.get("searxng_url") or "").rstrip("/")
        if not base:
            raise RuntimeError("no SearXNG address set")
        r = httpx.get(f"{base}/search", params={"q": q, "format": "json"}, timeout=15)
        r.raise_for_status()
        d = r.json()
        for eng, why in d.get("unresponsive_engines") or []:
            self._note(f"searxng {eng}: {why}")
        return [SearchHit(x.get("url", ""), x.get("title", ""), x.get("content", ""),
                          ",".join(x.get("engines") or []))
                for x in (d.get("results") or [])[:n]]

    # DuckDuckGo blocks bursts. Calls go one at a time with a gap, and after a
    # CAPTCHA plain requests are skipped for a while in favour of webclaw.
    _ddg_lock = threading.Lock()
    _ddg_last = 0.0
    _ddg_captcha_until = 0.0
    DDG_GAP = 1.5
    DDG_COOLDOWN = 600

    def _ddg(self, q, n):
        wc = self.cfg.get("webclaw_path") or shutil.which("webclaw")
        with Searcher._ddg_lock:
            wait = Searcher._ddg_last + self.DDG_GAP - time.time()
            if wait > 0:
                time.sleep(wait)
            try:
                if time.time() >= Searcher._ddg_captcha_until or not wc:
                    r = httpx.post("https://html.duckduckgo.com/html/", data={"q": q},
                                   headers={"User-Agent": UA}, timeout=15)
                    r.raise_for_status()
                    out = _parse_ddg_html(r.text, n)
                    if out:
                        return out
                    if "anomaly" not in r.text.lower():
                        return []
                    Searcher._ddg_captcha_until = time.time() + self.DDG_COOLDOWN
                    if not wc:
                        raise RuntimeError("DuckDuckGo asked for a CAPTCHA")
                p = subprocess.run([wc, "-f", "markdown", "-t", "20",
                                    "https://html.duckduckgo.com/html/?" + urlencode({"q": q})],
                                   capture_output=True, text=True, timeout=35)
                out = _parse_ddg_markdown(p.stdout, n)
                if not out and "anomaly" in p.stdout.lower():
                    raise RuntimeError("DuckDuckGo asked for a CAPTCHA, webclaw too")
                return out
            finally:
                Searcher._ddg_last = time.time()

    def _brave(self, q, n):
        r = httpx.get("https://api.search.brave.com/res/v1/web/search",
                      params={"q": q, "count": n},
                      headers={"X-Subscription-Token": self.cfg.get("brave", {}).get("api_key", ""),
                               "Accept": "application/json"}, timeout=15)
        r.raise_for_status()
        return [SearchHit(x.get("url", ""), x.get("title", ""), x.get("description", ""), "brave")
                for x in (r.json().get("web", {}).get("results") or [])[:n]]

    def _tavily(self, q, n):
        r = httpx.post("https://api.tavily.com/search", timeout=20,
                       json={"api_key": self.cfg.get("tavily", {}).get("api_key", ""),
                             "query": q, "max_results": n})
        r.raise_for_status()
        return [SearchHit(x.get("url", ""), x.get("title", ""), x.get("content", ""), "tavily")
                for x in (r.json().get("results") or [])[:n]]


# ── Text extraction ──────────────────────────────────────────────────
def html_to_text(html: str, url: str = "") -> tuple[str, str, str]:
    """Main text as Markdown, plus title and published date when the page has them."""
    import trafilatura
    text = trafilatura.extract(html, url=url or None, output_format="markdown",
                               include_tables=True, include_links=False,
                               include_comments=False, favor_recall=True) or ""
    title = published = ""
    try:
        md = trafilatura.extract_metadata(html, default_url=url or None)
        if md:
            title, published = md.title or "", md.date or ""
    except Exception:
        pass
    return text, title, published


def pdf_to_text(data: bytes) -> str:
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data))
    return "\n\n".join((p.extract_text() or "") for p in reader.pages[:60])


def looks_blocked(text: str) -> bool:
    return len(text) < 3000 and bool(_BLOCK_SIGNS.search(text))


def usable(text: str) -> bool:
    return len(text.strip()) >= MIN_TEXT and not looks_blocked(text)


# ── Reader ───────────────────────────────────────────────────────────
class Reader:
    def __init__(self, cfg: dict, cache_dir: Path | None = None):
        self.cfg = cfg
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_days = float(cfg.get("cache_days", 3))
        self.webclaw = cfg.get("webclaw_path") or shutil.which("webclaw")
        self.use_browser = bool(cfg.get("browser", True))
        self.gap = float(cfg.get("per_site_gap", 1.0))
        self._host_locks: dict[str, threading.Lock] = {}
        self._host_last: dict[str, float] = {}
        self._guard = threading.Lock()
        self._pw = None
        self._browser = None
        self._browser_lock = threading.Lock()
        self._browser_pool = None                # Playwright's sync API only works on the thread that started it
        self.stats = {"cache": 0, "fetch": 0, "webclaw": 0, "browser": 0, "blocked": 0}

    # one site at a time, with a gap between requests
    def _polite(self, url: str):
        h = host(url)
        with self._guard:
            lock = self._host_locks.setdefault(h, threading.Lock())
        lock.acquire()
        wait = self._host_last.get(h, 0) + self.gap - time.time()
        if wait > 0:
            time.sleep(wait)
        return lock, h

    def _done(self, lock, h):
        self._host_last[h] = time.time()
        lock.release()

    def read(self, url: str, fast: bool = False) -> Page:
        """fast: short timeouts and no browser, for a quick answer."""
        if not public_url(url):
            return Page(url, False, error="not a public web address")
        tried = []
        cached = self._cache_get(url)
        if cached:
            self.stats["cache"] += 1
            return cached
        wait = 8 if fast else 20
        steps = [("fetch", lambda u: self._fetch(u, wait))]
        if self.webclaw:
            steps.append(("webclaw", lambda u: self._webclaw(u, wait)))
        if self.use_browser and not fast:
            steps.append(("browser", self._browser_read))
        last_err = ""
        for name, fn in steps:
            tried.append(name)
            lock, h = self._polite(url)
            try:
                page = fn(readable_url(url))
                page.url = url
            except Exception as e:
                page = Page(url, False, error=f"{name}: {e}")
            finally:
                self._done(lock, h)
            if page.ok and usable(page.text):
                page.retrieval = page.retrieval or name
                page.tried = tuple(tried)
                self.stats[name] += 1
                self._cache_put(page)
                return page
            last_err = page.error or ("blocked" if looks_blocked(page.text) else "too little text")
            if page.error.startswith("fetch: http 404") or page.error == "http 404":
                break                       # gone, no point trying harder
            if "non-public address" in page.error:
                break                       # the site points inward: never retry it another way
        self.stats["blocked"] += 1
        return Page(url, False, error=last_err, retrieval="blocked", tried=tuple(tried))

    def _fetch(self, url: str, timeout: float = 20) -> Page:
        hdrs = {"User-Agent": UA, "Accept-Language": "en-GB,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.8"}
        with httpx.Client(follow_redirects=False, timeout=timeout, headers=hdrs) as c:
            cur = url
            for _ in range(6):
                r = c.send(c.build_request("GET", cur), stream=True)
                if r.is_redirect:
                    nxt = str(r.next_request.url) if r.next_request else ""
                    r.close()
                    if not nxt or not public_url(nxt):
                        return Page(url, False, error="redirect to a non-public address")
                    cur = nxt
                    continue
                break
            try:
                if r.status_code >= 400:
                    return Page(url, False, error=f"http {r.status_code}")
                data = b""
                for chunk in r.iter_bytes():   # never hold more than the cap
                    data += chunk
                    if len(data) >= MAX_BYTES:
                        data = data[:MAX_BYTES]
                        break
                ctype = r.headers.get("content-type", "").lower()
                enc = r.encoding or "utf-8"
            finally:
                r.close()
            if "pdf" in ctype or data[:5] == b"%PDF-":
                return Page(url, True, pdf_to_text(data), retrieval="pdf")
            text, title, pub = html_to_text(data.decode(enc, errors="replace"), cur)
            return Page(url, True, text, title, pub)

    def _webclaw(self, url: str, timeout: float = 30) -> Page:
        p = subprocess.run([self.webclaw, "-f", "markdown", "-t", str(int(timeout)), url],
                           capture_output=True, text=True, timeout=timeout + 15)
        if p.returncode != 0:
            return Page(url, False, error=f"webclaw exit {p.returncode}")
        text = p.stdout.strip()
        m = re.match(r"#\s+(.+)", text)
        return Page(url, True, text, m.group(1).strip() if m else "")

    def _browser_read(self, url: str) -> Page:
        with self._browser_lock:
            if self._browser_pool is None:
                from concurrent.futures import ThreadPoolExecutor
                self._browser_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="research-browser")
            pool = self._browser_pool
        return pool.submit(self._browser_job, url).result(timeout=90)

    def _browser_job(self, url: str) -> Page:
        if self._browser is None:
            from playwright.sync_api import sync_playwright
            self._pw = sync_playwright().start()
            # no GPU: a software-rendered GPU process can pin the CPU for hours
            self._browser = self._pw.chromium.launch(
                args=["--disable-gpu", "--disable-software-rasterizer"])
        ctx = self._browser.new_context(user_agent=UA, locale="en-GB",
                                        java_script_enabled=True)
        # Every request the page makes (redirects, frames, images, scripts)
        # must go to a public address, or it is dropped.
        allowed: dict[str, bool] = {}

        def _guard(route):
            target = route.request.url
            host = (urlparse(target).hostname or "").lower()
            if host not in allowed:
                allowed[host] = target.startswith(("data:", "blob:")) or public_url(target)
            if allowed[host]:
                route.continue_()
            else:
                route.abort("blockedbyclient")

        ctx.route("**/*", _guard)
        try:
            pg = ctx.new_page()
            pg.goto(url, wait_until="domcontentloaded", timeout=30000)
            try:
                pg.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass
            html = pg.content()
        finally:
            ctx.close()
        text, title, pub = html_to_text(html, url)
        return Page(url, True, text, title, pub)

    def close(self) -> None:
        """Always called at the end of a run: no browser is left running."""
        with self._browser_lock:
            pool, self._browser_pool = self._browser_pool, None
        if pool is None:
            return
        try:
            pool.submit(self._browser_stop).result(timeout=30)
        finally:
            pool.shutdown(wait=False)

    def _browser_stop(self) -> None:
        try:
            if self._browser:
                self._browser.close()
        finally:
            if self._pw:
                self._pw.stop()
            self._browser = self._pw = None

    # ── cache ────────────────────────────────────────────────────────
    def _cache_file(self, url: str) -> Path | None:
        if not self.cache_dir:
            return None
        return self.cache_dir / (hashlib.sha256(clean_url(url).encode()).hexdigest()[:32] + ".json")

    def _cache_get(self, url: str) -> Page | None:
        f = self._cache_file(url)
        if not f or not f.exists() or time.time() - f.stat().st_mtime > self.cache_days * 86400:
            return None
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return Page(url, True, d["text"], d.get("title", ""), d.get("published", ""),
                    retrieval="cache", tried=("cache",))

    def _cache_put(self, page: Page) -> None:
        f = self._cache_file(page.url)
        if f:
            f.write_text(json.dumps({"url": page.url, "text": page.text, "title": page.title,
                                     "published": page.published}), encoding="utf-8")
