import json
import re
import time
import random
from contextlib import contextmanager
from datetime import datetime
from urllib.parse import urlencode, urlparse, parse_qs, urljoin

from bs4 import BeautifulSoup, Tag
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

from make_model_utils import extract_make_model

BASE_URL = "https://www.ebay.com"

_stop_requested = False


def request_stop() -> None:
    global _stop_requested
    _stop_requested = True


def clear_stop() -> None:
    global _stop_requested
    _stop_requested = False
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


# ---------------------------------------------------------------------------
# Browser lifecycle
# ---------------------------------------------------------------------------

@contextmanager
def _browser_session():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        ctx = browser.new_context(
            user_agent=_UA,
            viewport={"width": 1280, "height": 800},
            locale="en-US",
        )
        ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        page = ctx.new_page()
        # Warm up — grab homepage to load cookies
        page.goto(BASE_URL, wait_until="domcontentloaded", timeout=20000)
        time.sleep(1.5)
        yield page
        browser.close()


def _fetch(page, url: str, wait_selector: str = "li.s-card") -> BeautifulSoup | None:
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=25000)
        try:
            page.wait_for_selector(wait_selector, timeout=10000)
        except PWTimeout:
            pass  # page may still have content
        time.sleep(random.uniform(1.0, 2.0))
        return BeautifulSoup(page.content(), "lxml")
    except Exception as exc:
        print(f"  Error loading {url}: {exc}")
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _has_next_page(soup: BeautifulSoup) -> bool:
    return bool(soup.select_one("a.pagination__next"))


def _fetch_with_retry(page, url: str, retries: int = 2) -> BeautifulSoup | None:
    """Fetch a results page, retrying with backoff on a failed/empty (likely
    throttled) response. Returns the last soup attempted (may have no cards)."""
    soup = None
    for attempt in range(retries + 1):
        soup = _fetch(page, url)
        if soup and soup.select_one("li.s-card"):
            return soup
        if attempt < retries:
            wait = random.uniform(4.0, 7.0) * (attempt + 1)
            print(f"  No cards (attempt {attempt + 1}/{retries + 1}) — "
                  f"backing off {wait:.0f}s and retrying")
            time.sleep(wait)
    return soup


def _paginated_collect(page, url_for_page, *, is_sold: bool, pages: int,
                       _on_page=None, seller: str | None = None) -> list[dict]:
    """Shared pagination loop: retries throttled pages and terminates when a
    page yields no NEW items (eBay re-serves the last page past the end)."""
    results: list[dict] = []
    seen: set[str] = set()
    auto = pages == 0
    max_p = 100 if auto else pages

    for p in range(1, max_p + 1):
        if _stop_requested:
            print("  Stop requested — halting.")
            break

        url = url_for_page(p)
        print(f"  Page {p}: {url}")

        soup = _fetch_with_retry(page, url)
        cards = soup.select("li.s-card") if soup else []
        if not cards:
            print(f"  Page {p}: no listing cards after retries — "
                  "stopping (end of results or blocked by eBay).")
            break

        new = 0
        for card in cards:
            data = _parse_card(card, is_sold=is_sold)
            if not data or data["url"] in seen:
                continue
            seen.add(data["url"])
            if seller and not data.get("seller"):
                data["seller"] = seller
            results.append(data)
            new += 1

        print(f"  Collected {len(results)} listings so far (+{new} new).")
        if _on_page:
            _on_page(page=p, collected=len(results))

        if new == 0:
            print("  No new items on this page — reached the end. Stopping.")
            break
        if auto and not _has_next_page(soup):
            print("  Last page reached.")
            break

    return results


def _clean_url(url: str) -> str:
    return url.split("?")[0] if url else ""


def _parse_price(text: str) -> float | None:
    if not text:
        return None
    text = text.split(" to ")[0]  # "10.00 to 20.00" → take lower bound
    m = re.search(r"[\d,]+\.?\d*", text.replace(",", ""))
    return float(m.group().replace(",", "")) if m else None


def _text(el: Tag | None) -> str | None:
    return el.get_text(strip=True) if el else None


_MONTHS = {"Jan":1,"Feb":2,"Mar":3,"Apr":4,"May":5,"Jun":6,
           "Jul":7,"Aug":8,"Sep":9,"Oct":10,"Nov":11,"Dec":12}

def _parse_listed_date(text: str, year: int) -> str | None:
    """Convert 'Jun-17' or 'Jun-17 04:56' → '17.06.2026'."""
    if not text:
        return None
    m = re.search(r"([A-Z][a-z]{2})-(\d{1,2})", text)
    if not m:
        return None
    mon = _MONTHS.get(m.group(1))
    if not mon:
        return None
    return f"{int(m.group(2)):02d}.{mon:02d}.{year}"


# ---------------------------------------------------------------------------
# Card parser  (new eBay SRP: li.s-card)
# ---------------------------------------------------------------------------

def _parse_card(item: Tag, is_sold: bool) -> dict | None:
    ebay_id = item.get("data-listingid")

    title_el = item.select_one(".s-card__title span.su-styled-text")
    title = _text(title_el)
    if not title or title.strip() == "Shop on eBay":
        return None
    title = re.sub(r"^New Listing\s*", "", title, flags=re.IGNORECASE).strip()

    link_el = item.select_one("a.s-card__link")
    url = link_el.get("href") if link_el else None
    if not url:
        return None

    img_el = item.select_one("img.s-card__image")
    image_url = img_el.get("src") if img_el else None

    cond_el = item.select_one(".s-card__subtitle span.su-styled-text")
    condition = _text(cond_el)

    # Some eBay cards put "Brand · Model · …" in the subtitle instead of a condition string
    make = model = None
    if condition and "·" in condition:
        parts = [p.strip() for p in condition.split("·") if p.strip()]
        make = parts[0] if len(parts) >= 1 else None
        model = parts[1] if len(parts) >= 2 else None
        condition = None

    # Fallback: extract from title when subtitle didn't yield make/model
    if not make:
        make, model = extract_make_model(title)

    price_el = item.select_one(".s-card__price")
    price = _parse_price(_text(price_el))

    # Sold date from caption
    sold_date = None
    caption_el = item.select_one(".s-card__caption span.su-styled-text")
    if caption_el:
        cap = _text(caption_el) or ""
        if "Sold" in cap:
            sold_date = cap.replace("Sold", "").strip()

    # Bids and shipping from attribute rows
    bids = None
    shipping = None
    for span in item.select(".s-card__attribute-row span.su-styled-text"):
        t = _text(span) or ""
        if bids is None and re.search(r"\bbid", t, re.IGNORECASE):
            m = re.search(r"(\d+)", t)
            bids = int(m.group(1)) if m else None
        elif shipping is None and re.search(r"delivery|shipping|^free", t, re.IGNORECASE):
            shipping = t

    # Seller name — first span in secondary attributes that isn't a date/time
    seller_spans = item.select(".su-card-container__attributes__secondary span.su-styled-text")
    seller = None
    for span in seller_spans:
        t = _text(span) or ""
        if re.search(r"[A-Z][a-z]{2}[-\s]\d{1,2}|\d{1,2}:\d{2}", t, re.IGNORECASE):
            continue
        if t:
            seller = t
            break

    # Listed date — scan all text spans in the card for a "Mon-DD" pattern
    now = datetime.now()
    listed_date = None
    for span in item.select("span.su-styled-text"):
        t = _text(span) or ""
        listed_date = _parse_listed_date(t, now.year)
        if listed_date:
            break

    return {
        "ebay_id": ebay_id,
        "url": _clean_url(url),
        "title": title,
        "price": price,
        "currency": "USD",
        "condition": condition,
        "image_url": image_url,
        "seller": seller,
        "make": make,
        "model": model,
        "location": None,
        "shipping": shipping,
        "bids": bids,
        "is_sold": 1 if is_sold else 0,
        "status": "Sold" if is_sold else "Active",
        "sold_date": sold_date,
        "listed_date": listed_date,
        "scraped_at": now.isoformat(),
    }


# ---------------------------------------------------------------------------
# Public scraping functions
# ---------------------------------------------------------------------------

def scrape_search(keyword: str, pages: int = 0, sold_only: bool = False,
                  _on_page=None) -> list[dict]:
    """Scrape search results. pages=0 auto-paginates all available pages (up to 100)."""
    def url_for_page(p: int) -> str:
        params: dict = {"_nkw": keyword, "_pgn": p, "_ipg": 240}
        if sold_only:
            params["LH_Complete"] = "1"
            params["LH_Sold"] = "1"
        return f"{BASE_URL}/sch/i.html?{urlencode(params)}"

    with _browser_session() as page:
        return _paginated_collect(page, url_for_page, is_sold=sold_only,
                                  pages=pages, _on_page=_on_page)


def scrape_seller(seller: str, pages: int = 0, _on_page=None) -> list[dict]:
    """Scrape seller listings. pages=0 auto-paginates all available pages."""
    def url_for_page(p: int) -> str:
        return f"{BASE_URL}/sch/{seller}/m.html?_pgn={p}&_ipg=240"

    with _browser_session() as page:
        return _paginated_collect(page, url_for_page, is_sold=False,
                                  pages=pages, _on_page=_on_page, seller=seller)


def scrape_product(url: str) -> tuple[dict | None, dict | None]:
    with _browser_session() as page:
        clean = _clean_url(url)
        print(f"  Fetching: {clean}")

        try:
            page.goto(clean, wait_until="domcontentloaded", timeout=25000)
            time.sleep(2)
        except Exception as exc:
            print(f"  Error: {exc}")
            return None, None

        soup = BeautifulSoup(page.content(), "lxml")
        ebay_id_m = re.search(r"/itm/(?:[^/]+/)?(\d+)", clean)
        ebay_id = ebay_id_m.group(1) if ebay_id_m else None
        now = datetime.now().isoformat()

        # Title
        title_el = (
            soup.select_one("h1.x-item-title__mainTitle span.ux-textspans")
            or soup.select_one("h1.x-item-title__mainTitle")
            or soup.select_one("h1")
        )
        title = _text(title_el) or "Unknown"
        title = re.sub(r"^Details about\s*\xa0?", "", title, flags=re.IGNORECASE).strip()

        # Price
        price_el = (
            soup.select_one(".x-price-primary .ux-textspans")
            or soup.select_one("[itemprop='price']")
        )
        price = _parse_price(_text(price_el))

        # Condition
        cond_el = (
            soup.select_one(".x-item-condition-value .ux-textspans")
            or soup.select_one("[itemprop='itemCondition']")
        )
        condition = _text(cond_el)

        # Seller
        seller_el = (
            soup.select_one(".x-sellercard-atf__info__about-seller a")
            or soup.select_one(".mbg-nw")
        )
        seller = _text(seller_el)

        # Image
        img_el = soup.select_one(".ux-image-carousel-item img, #icImg")
        image_url = (img_el.get("src") or img_el.get("data-src")) if img_el else None

        # Location + shipping from label-value pairs
        location = shipping = None
        for row in soup.select(".ux-labels-values"):
            label = (_text(row.select_one(".ux-labels-values__labels")) or "").lower()
            value = _text(row.select_one(".ux-labels-values__values"))
            if "item location" in label and not location:
                location = value
            elif "shipping" in label and not shipping:
                shipping = value

        # Item specifics (all label→value pairs)
        specifics: dict[str, str] = {}
        for row in soup.select(".ux-labels-values"):
            label = _text(row.select_one(".ux-labels-values__labels"))
            value = _text(row.select_one(".ux-labels-values__values"))
            if label and value:
                specifics[label.rstrip(":")] = value

        # Feedback
        feedback_score = feedback_pct = None
        fscore_el = soup.select_one(".x-sellercard-atf__data-item--feedback .ux-textspans")
        if fscore_el:
            m = re.search(r"([\d,]+)", fscore_el.get_text())
            if m:
                feedback_score = int(m.group(1).replace(",", ""))
        fpct_el = soup.select_one(".x-sellercard-atf__data-item--positive .ux-textspans")
        if fpct_el:
            m = re.search(r"([\d.]+)%", fpct_el.get_text())
            if m:
                feedback_pct = float(m.group(1))

        make = specifics.get("Brand") or specifics.get("Make")
        model = specifics.get("Model")
        # Fallback to title parsing when item specifics don't have make/model
        if not make:
            make, title_model = extract_make_model(title)
            if not model:
                model = title_model

        listing = {
            "ebay_id": ebay_id,
            "url": clean,
            "title": title,
            "price": price,
            "currency": "USD",
            "condition": condition,
            "image_url": image_url,
            "seller": seller,
            "make": make,
            "model": model,
            "location": location,
            "shipping": shipping,
            "bids": None,
            "is_sold": 0,
            "status": "Active",
            "sold_date": None,
            "scraped_at": now,
        }

        detail = {
            "listing_id": None,
            "description": None,
            "item_specifics": json.dumps(specifics) if specifics else None,
            "buy_it_now_price": None,
            "auction_end_time": None,
            "seller_feedback_score": feedback_score,
            "seller_feedback_percent": feedback_pct,
            "scraped_at": now,
        }

        return listing, detail


def scrape_from_url(url: str, pages: int = 0, _on_page=None) -> list[dict]:
    """Scrape listings from any eBay search/browse URL. Paginates via _pgn param."""
    parsed = urlparse(url)
    qs = parse_qs(parsed.query, keep_blank_values=True)
    is_sold = bool(qs.get("LH_Sold") or qs.get("LH_Complete"))
    qs["_sop"] = ["10"]   # sort: newly listed
    qs["_ipg"] = ["240"]  # 240 items per page (eBay max)

    def url_for_page(p: int) -> str:
        page_qs = {k: v[0] for k, v in qs.items()}
        page_qs["_pgn"] = str(p)
        return parsed._replace(query=urlencode(page_qs)).geturl()

    with _browser_session() as page:
        return _paginated_collect(page, url_for_page, is_sold=is_sold,
                                  pages=pages, _on_page=_on_page)
