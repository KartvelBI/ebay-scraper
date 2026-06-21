import json
import os
import threading
from urllib.parse import urlparse, parse_qs, urlencode

from flask import Flask, flash, jsonify, redirect, render_template, request, url_for

import database as db
import scraper as sc

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or os.urandom(24)


@app.context_processor
def _inject_job_state():
    with _JOB_LOCK:
        return {"scrape_running": _JOB.get("running", False)}

# ---------------------------------------------------------------------------
# Background job state  (single-worker, threaded=True safe)
# ---------------------------------------------------------------------------

_JOB: dict = {
    "running": False,
    "done": True,
    "task": "",
    "detail": "",
    "page": 0,
    "collected": 0,
    "saved": 0,
    "log": [],
    "error": None,
    "stop_requested": False,
}
_JOB_LOCK = threading.Lock()


def _jset(**kw) -> None:
    with _JOB_LOCK:
        _JOB.update(kw)


def _jlog(msg: str) -> None:
    print(msg)
    with _JOB_LOCK:
        _JOB["log"].append(msg)
        if len(_JOB["log"]) > 200:
            _JOB["log"] = _JOB["log"][-200:]


def _on_page(page: int, collected: int) -> None:
    _jlog(f"Page {page} — {collected} items collected so far")
    _jset(page=page, collected=collected)


def _make_batch_saver(store_name=None, base: int = 0):
    """Return an _on_batch(listings) callback that saves each page as it is
    scraped, so collected data is persisted incrementally and survives an
    early stop or crash. `base` offsets the live counter for multi-part jobs.
    The returned callable exposes `.total` (rows saved by this saver)."""
    def _save(batch: list[dict]) -> None:
        n = _bulk_save(batch, store_name=store_name)
        _save.total += n
        _jset(saved=base + _save.total)
        _jlog(f"Saved {n} this page (running total: {base + _save.total})")
    _save.total = 0
    return _save


# Background worker functions

def _run_search(keyword: str, pages: int, sold: bool, store_name) -> None:
    saver = _make_batch_saver(store_name)
    try:
        _jset(running=True, done=False, task="Search", detail=keyword,
              page=0, collected=0, saved=0, log=[], error=None, stop_requested=False)
        _jlog(f'Scraping eBay search: "{keyword}"')
        sc.clear_stop()
        listings = sc.scrape_search(keyword, pages=pages, sold_only=sold,
                                    _on_page=_on_page, _on_batch=saver)
        _jlog(f"Done — {len(listings)} scraped, {saver.total} saved to database.")
        _jset(saved=saver.total, done=True, running=False)
    except Exception as exc:
        _jlog(f"Error (saved {saver.total} before failing): {exc}")
        _jset(saved=saver.total, error=str(exc), done=True, running=False)


def _run_seller(sellers: list, store_names: list, pages: int) -> None:
    try:
        total = len(sellers)
        _jset(running=True, done=False, task="Seller", detail=f"{total} seller(s)",
              page=0, collected=0, saved=0, log=[], error=None, stop_requested=False)
        sc.clear_stop()
        total_saved = 0
        for i, seller in enumerate(sellers):
            if sc._stop_requested:
                _jlog("Stopped by user.")
                break
            store_name = (store_names[i].strip() if i < len(store_names) else "") or seller
            _jset(detail=f"{seller} ({i+1}/{total})", page=0, collected=0)
            _jlog(f"--- Seller {i+1}/{total}: {seller} (store: {store_name}) ---")
            saver = _make_batch_saver(store_name, base=total_saved)
            listings = sc.scrape_seller(seller, pages=pages,
                                        _on_page=_on_page, _on_batch=saver)
            total_saved += saver.total
            _jlog(f"Saved {saver.total} for {seller} | total so far: {total_saved}")
            _jset(saved=total_saved)
        _jlog(f"All done — {total_saved} total listings saved.")
        _jset(done=True, running=False)
    except Exception as exc:
        _jlog(f"Error: {exc}")
        _jset(error=str(exc), done=True, running=False)


def _run_url_scrape(url: str, pages: int, store_name) -> None:
    saver = _make_batch_saver(store_name)
    try:
        _jset(running=True, done=False, task="URL Scrape", detail=url,
              page=0, collected=0, saved=0, log=[], error=None, stop_requested=False)
        _jlog(f"Scraping eBay URL: {url}")
        sc.clear_stop()
        listings = sc.scrape_from_url(url, pages=pages, _on_page=_on_page, _on_batch=saver)
        _jlog(f"Done — {len(listings)} scraped, {saver.total} saved to database.")
        _jset(saved=saver.total, done=True, running=False)
    except Exception as exc:
        _jlog(f"Error (saved {saver.total} before failing): {exc}")
        _jset(saved=saver.total, error=str(exc), done=True, running=False)


def _run_product(url: str, store_name) -> None:
    try:
        _jset(running=True, done=False, task="Product URL", detail=url,
              page=0, collected=0, saved=0, log=[], error=None, stop_requested=False)
        _jlog(f"Scraping: {url}")
        sc.clear_stop()
        listing, detail = sc.scrape_product(url)
        if not listing:
            _jlog("Failed to scrape that URL.")
            _jset(error="Could not scrape the URL — check it is a valid eBay listing.", done=True, running=False)
            return
        listing["store_name"] = store_name
        listing_id = db.upsert_listing(listing)
        if detail:
            detail["listing_id"] = listing_id
            db.upsert_product_detail(detail)
        _jlog(f'Saved: "{listing["title"][:70]}"')
        _jset(saved=1, collected=1, done=True, running=False)
    except Exception as exc:
        _jlog(f"Error: {exc}")
        _jset(error=str(exc), done=True, running=False)


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    db.init_db()
    stats  = db.get_stats()
    recent = db.get_recent_listings(10)
    return render_template("index.html", stats=stats, recent=recent)


# ---------------------------------------------------------------------------
# Listings browser
# ---------------------------------------------------------------------------

@app.route("/listings")
def listings():
    db.init_db()
    keyword    = request.args.get("keyword", "").strip()
    seller     = request.args.get("seller", "").strip()
    store_name = request.args.get("store_name", "").strip()
    make       = request.args.get("make", "").strip()
    model      = request.args.get("model", "").strip()
    status     = request.args.get("status", "")
    limit      = int(request.args.get("limit", 50))

    rows, total = db.query_listings(
        keyword=keyword or None,
        seller=seller or None,
        store_name=store_name or None,
        make=make or None,
        model=model or None,
        sold_only=(status == "sold"),
        active_only=(status == "active"),
        limit=limit,
    )
    return render_template(
        "listings.html",
        rows=rows,
        total=total,
        filters={"keyword": keyword, "seller": seller, "store_name": store_name,
                 "make": make, "model": model, "status": status, "limit": limit},
    )


# ---------------------------------------------------------------------------
# Listing detail
# ---------------------------------------------------------------------------

@app.route("/listing/<int:listing_id>")
def listing_detail(listing_id):
    db.init_db()
    src = request.args.get("src", "")
    source = {"sold": db.SOLD_TABLE, "active": db.ACTIVE_TABLE}.get(src)
    listing = db.get_listing_by_id(listing_id, source=source)
    if not listing:
        flash("Listing not found.", "error")
        return redirect(url_for("listings"))

    detail    = db.query_product_detail(listing_id)
    specifics = {}
    if detail:
        raw = detail.get("item_specifics")
        # Supabase JSONB returns a dict; SQLite would return a JSON string
        if isinstance(raw, dict):
            specifics = raw
        elif isinstance(raw, str):
            try:
                specifics = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                pass

    return render_template("listing_detail.html", listing=listing, detail=detail, specifics=specifics)


# ---------------------------------------------------------------------------
# Scrape — Search
# ---------------------------------------------------------------------------

_SEARCH_FIELDS = """
<div class="mb-3">
  <label class="form-label fw-semibold">Keyword <span class="text-danger">*</span></label>
  <input name="keyword" class="form-control" placeholder='e.g. "vintage rolex"' required />
</div>
<div class="mb-3">
  <label class="form-label fw-semibold">Store Name <span class="text-muted fw-normal">(optional label for this scrape)</span></label>
  <input name="store_name" class="form-control" placeholder='e.g. "Rolex Market Research"' />
</div>
<div class="mb-3">
  <label class="form-label fw-semibold">Pages to scrape</label>
  <select name="pages" class="form-select" id="pagesSelect" onchange="toggleCustomPages(this)">
    <option value="0">All pages (auto)</option>
    <option value="1">1 page (~240 items)</option>
    <option value="3">3 pages (~720 items)</option>
    <option value="5">5 pages (~1,200 items)</option>
    <option value="10">10 pages (~2,400 items)</option>
    <option value="custom">Custom…</option>
  </select>
</div>
<div class="mb-3 d-none" id="customPagesWrap">
  <label class="form-label fw-semibold">Custom page count</label>
  <input name="pages_custom" type="number" class="form-control" value="2" min="1" max="100" />
</div>
<div class="form-check mb-2">
  <input class="form-check-input" type="checkbox" name="sold" id="sold" value="1" />
  <label class="form-check-label fw-semibold" for="sold">Sold / completed only</label>
</div>
<script>
function toggleCustomPages(sel) {
  document.getElementById('customPagesWrap').classList.toggle('d-none', sel.value !== 'custom');
}
</script>
"""

@app.route("/scrape/search", methods=["GET", "POST"])
def scrape_search():
    if request.method == "POST":
        if _JOB["running"]:
            flash("A scrape is already in progress — stop it first or wait.", "error")
            return redirect(url_for("scrape_progress_page"))
        keyword    = request.form.get("keyword", "").strip()
        store_name = request.form.get("store_name", "").strip() or None
        pages      = _parse_pages(request.form)
        sold       = bool(request.form.get("sold"))
        if not keyword:
            flash("Please enter a keyword.", "error")
            return redirect(url_for("scrape_search"))
        _jset(running=True, done=False, task="Search", detail=keyword,
              page=0, collected=0, saved=0, log=[], error=None, stop_requested=False)
        threading.Thread(target=_run_search, args=(keyword, pages, sold, store_name), daemon=True).start()
        return redirect(url_for("scrape_progress_page"))

    return render_template(
        "scrape.html",
        form_title="Search eBay",
        form_desc="Scrape listings by keyword. Tick 'Sold only' to collect historical sold prices.",
        card_class="search-card",
        form_fields=_SEARCH_FIELDS,
        tips=[
            "Use specific keywords for better results.",
            "Sold/completed items are great for price research.",
            "Each page returns ~240 listings.",
            "'All pages (auto)' keeps going until eBay has no more results.",
            "Large searches (1000+ items) can take a few minutes — the button will spin.",
        ],
    )


# ---------------------------------------------------------------------------
# Scrape — Sold items only
# ---------------------------------------------------------------------------

def _force_sold_url(url: str) -> str:
    """Add eBay's 'Sold Items' filter (LH_Sold=1) to any search/store URL and
    drop 'Completed Items' so it matches the Sold Items checkbox exactly."""
    parsed = urlparse(url)
    qs = parse_qs(parsed.query, keep_blank_values=True)
    qs["LH_Sold"] = ["1"]
    qs.pop("LH_Complete", None)
    flat = {k: v[0] for k, v in qs.items()}
    return parsed._replace(query=urlencode(flat)).geturl()


_SOLD_FIELDS = """
<div class="mb-3">
  <label class="form-label fw-semibold">eBay search / store URL <span class="text-danger">*</span></label>
  <input name="url" class="form-control" placeholder="https://www.ebay.com/sch/i.html?_ssn=autohubshop" required />
  <div class="form-text">Paste any eBay search or store URL — the <strong>Sold Items</strong> filter is applied automatically.</div>
</div>
<div class="mb-3">
  <label class="form-label fw-semibold">Store Name <span class="text-muted fw-normal">(optional label for this scrape)</span></label>
  <input name="store_name" class="form-control" placeholder='e.g. "autohubshop"' />
</div>
<div class="mb-3">
  <label class="form-label fw-semibold">Pages to scrape</label>
  <select name="pages" class="form-select" id="pagesSelect" onchange="toggleCustomPages(this)">
    <option value="0">All pages (auto)</option>
    <option value="1">1 page (~240 items)</option>
    <option value="3">3 pages (~720 items)</option>
    <option value="5">5 pages (~1,200 items)</option>
    <option value="10">10 pages (~2,400 items)</option>
    <option value="custom">Custom…</option>
  </select>
</div>
<div class="mb-3 d-none" id="customPagesWrap">
  <label class="form-label fw-semibold">Custom page count</label>
  <input name="pages_custom" type="number" class="form-control" value="2" min="1" max="100" />
</div>
<div class="alert border flash-success mb-0" style="font-size:.85rem;">
  <i class="bi bi-check-circle me-1"></i> Scrapes <strong>sold</strong> listings only and saves them to the <strong>sold</strong> table.
</div>
<script>
function toggleCustomPages(sel) {
  document.getElementById('customPagesWrap').classList.toggle('d-none', sel.value !== 'custom');
}
</script>
"""

@app.route("/scrape/sold", methods=["GET", "POST"])
def scrape_sold():
    if request.method == "POST":
        if _JOB["running"]:
            flash("A scrape is already in progress — stop it first or wait.", "error")
            return redirect(url_for("scrape_progress_page"))
        url        = request.form.get("url", "").strip()
        store_name = request.form.get("store_name", "").strip() or None
        pages      = _parse_pages(request.form)
        if not url:
            flash("Please enter an eBay URL.", "error")
            return redirect(url_for("scrape_sold"))
        sold_url = _force_sold_url(url)
        _jset(running=True, done=False, task="Sold Items", detail=sold_url,
              page=0, collected=0, saved=0, log=[], error=None, stop_requested=False)
        # scrape_from_url detects LH_Sold and routes results to the sold table.
        threading.Thread(target=_run_url_scrape, args=(sold_url, pages, store_name), daemon=True).start()
        return redirect(url_for("scrape_progress_page"))

    return render_template(
        "scrape.html",
        form_title="Scrape Sold Items",
        form_desc="Paste an eBay search or store URL — sold listings are scraped and saved to the sold table.",
        card_class="search-card",
        form_fields=_SOLD_FIELDS,
        tips=[
            "Works with any eBay search or seller-store URL.",
            "eBay's 'Sold Items' filter (LH_Sold=1) is forced automatically.",
            "Each row is saved with status = Sold into the sold table.",
            "'All pages (auto)' keeps going until eBay runs out of results.",
        ],
    )


# ---------------------------------------------------------------------------
# Scrape — Seller
# ---------------------------------------------------------------------------

_SELLER_FIELDS = """
<div class="mb-2">
  <div class="d-flex justify-content-between align-items-center mb-2">
    <label class="form-label fw-semibold mb-0">Sellers</label>
    <span class="text-muted small">Scraped one by one in order</span>
  </div>

  <div class="mb-2 d-flex gap-2 align-items-center" style="font-size:.78rem;color:#6c757d;font-weight:600;">
    <div style="flex:1;">USERNAME</div>
    <div style="flex:1;">STORE NAME <span style="font-weight:400;">(optional)</span></div>
    <div style="width:36px;"></div>
  </div>

  <div id="seller-rows">
    <div class="seller-row mb-2 d-flex gap-2 align-items-center">
      <input name="seller" class="form-control" placeholder="e.g. best_seller_123" />
      <input name="store_name" class="form-control" placeholder="e.g. Apple Reseller UK" />
      <button type="button" class="btn btn-outline-danger btn-sm remove-row" style="min-width:36px;font-size:1.1rem;line-height:1;">−</button>
    </div>
    <div class="seller-row mb-2 d-flex gap-2 align-items-center">
      <input name="seller" class="form-control" placeholder="e.g. best_seller_123" />
      <input name="store_name" class="form-control" placeholder="e.g. Apple Reseller UK" />
      <button type="button" class="btn btn-outline-danger btn-sm remove-row" style="min-width:36px;font-size:1.1rem;line-height:1;">−</button>
    </div>
    <div class="seller-row mb-2 d-flex gap-2 align-items-center">
      <input name="seller" class="form-control" placeholder="e.g. best_seller_123" />
      <input name="store_name" class="form-control" placeholder="e.g. Apple Reseller UK" />
      <button type="button" class="btn btn-outline-danger btn-sm remove-row" style="min-width:36px;font-size:1.1rem;line-height:1;">−</button>
    </div>
  </div>

  <button type="button" id="add-seller-row" class="btn btn-outline-secondary btn-sm mt-1">
    <i class="bi bi-plus-lg me-1"></i> Add seller
  </button>
</div>

<div class="mb-3 mt-3">
  <label class="form-label fw-semibold">Pages per seller</label>
  <select name="pages" class="form-select" id="pagesSelect" onchange="toggleCustomPages(this)">
    <option value="0">All pages (auto)</option>
    <option value="1">1 page (~240 items)</option>
    <option value="3">3 pages (~720 items)</option>
    <option value="5">5 pages (~1,200 items)</option>
    <option value="10">10 pages (~2,400 items)</option>
    <option value="custom">Custom…</option>
  </select>
</div>
<div class="mb-3 d-none" id="customPagesWrap">
  <label class="form-label fw-semibold">Custom page count</label>
  <input name="pages_custom" type="number" class="form-control" value="2" min="1" max="100" />
</div>

<script>
function toggleCustomPages(sel) {
  document.getElementById('customPagesWrap').classList.toggle('d-none', sel.value !== 'custom');
}

function makeRow() {
  const div = document.createElement('div');
  div.className = 'seller-row mb-2 d-flex gap-2 align-items-center';
  div.innerHTML = `
    <input name="seller" class="form-control" placeholder="e.g. best_seller_123" />
    <input name="store_name" class="form-control" placeholder="e.g. Apple Reseller UK" />
    <button type="button" class="btn btn-outline-danger btn-sm remove-row" style="min-width:36px;font-size:1.1rem;line-height:1;">−</button>
  `;
  return div;
}

document.getElementById('add-seller-row').addEventListener('click', function () {
  document.getElementById('seller-rows').appendChild(makeRow());
});

document.getElementById('seller-rows').addEventListener('click', function (e) {
  if (e.target.classList.contains('remove-row')) {
    const rows = document.querySelectorAll('.seller-row');
    if (rows.length > 1) e.target.closest('.seller-row').remove();
  }
});
</script>
"""

@app.route("/scrape/seller", methods=["GET", "POST"])
def scrape_seller():
    if request.method == "POST":
        sellers     = [s.strip() for s in request.form.getlist("seller") if s.strip()]
        store_names = request.form.getlist("store_name")
        pages       = _parse_pages(request.form)

        if not sellers:
            flash("Please enter at least one seller username.", "error")
            return redirect(url_for("scrape_seller"))

        if _JOB["running"]:
            flash("A scrape is already in progress — stop it first or wait.", "error")
            return redirect(url_for("scrape_progress_page"))
        _jset(running=True, done=False, task="Seller", detail=f"{len(sellers)} seller(s)",
              page=0, collected=0, saved=0, log=[], error=None, stop_requested=False)
        threading.Thread(target=_run_seller, args=(sellers, store_names, pages), daemon=True).start()
        return redirect(url_for("scrape_progress_page"))

    return render_template(
        "scrape.html",
        form_title="Scrape Sellers",
        form_desc="Scrape listings from multiple eBay sellers. Each seller is scraped one by one.",
        card_class="seller-card",
        form_fields=_SELLER_FIELDS,
        tips=[
            "Add up to as many sellers as you need with the + button.",
            "Store Name lets you tag each seller with a friendly label.",
            "If Store Name is left blank, the seller username is used.",
            "Pages per seller applies to all sellers equally.",
            "Sellers are scraped in order — the button spins until all are done.",
        ],
    )


# ---------------------------------------------------------------------------
# Scrape — Product URL
# ---------------------------------------------------------------------------

_PRODUCT_FIELDS = """
<div class="mb-3">
  <label class="form-label fw-semibold">eBay listing URL <span class="text-danger">*</span></label>
  <input name="url" class="form-control" placeholder="https://www.ebay.com/itm/..." required />
  <div class="form-text">Paste the full URL of any eBay listing.</div>
</div>
<div class="mb-3">
  <label class="form-label fw-semibold">Store Name <span class="text-muted fw-normal">(optional)</span></label>
  <input name="store_name" class="form-control" placeholder='e.g. "Apple UK Official"' />
</div>
"""

@app.route("/scrape/product", methods=["GET", "POST"])
def scrape_product():
    if request.method == "POST":
        url        = request.form.get("url", "").strip()
        store_name = request.form.get("store_name", "").strip() or None
        if not url:
            flash("Please enter a URL.", "error")
            return redirect(url_for("scrape_product"))

        if _JOB["running"]:
            flash("A scrape is already in progress — stop it first or wait.", "error")
            return redirect(url_for("scrape_progress_page"))
        _jset(running=True, done=False, task="Product URL", detail=url,
              page=0, collected=0, saved=0, log=[], error=None, stop_requested=False)
        threading.Thread(target=_run_product, args=(url, store_name), daemon=True).start()
        return redirect(url_for("scrape_progress_page"))

    return render_template(
        "scrape.html",
        form_title="Scrape Product URL",
        form_desc="Scrape a single eBay listing for full details, item specifics, and seller info.",
        card_class="product-card",
        form_fields=_PRODUCT_FIELDS,
        tips=[
            "Works with any active or completed eBay listing URL.",
            "Fetches item specifics, seller feedback, auction details, and more.",
            "Re-scraping the same URL updates the record in place.",
        ],
    )


# ---------------------------------------------------------------------------
# Scrape — URL
# ---------------------------------------------------------------------------

_URL_FIELDS = """
<div class="mb-3">
  <label class="form-label fw-semibold">eBay search / browse URL <span class="text-danger">*</span></label>
  <input name="url" class="form-control" placeholder="https://www.ebay.com/sch/i.html?_nkw=rolex&LH_Sold=1" required />
  <div class="form-text">Paste any eBay search or browse URL — sold/active is detected automatically.</div>
</div>
<div class="mb-3">
  <label class="form-label fw-semibold">Store Name <span class="text-muted fw-normal">(optional)</span></label>
  <input name="store_name" class="form-control" placeholder='e.g. "Rolex Research"' />
</div>
<div class="mb-3">
  <label class="form-label fw-semibold">Pages to scrape</label>
  <select name="pages" class="form-select" id="pagesSelect" onchange="toggleCustomPages(this)">
    <option value="0">All pages (auto)</option>
    <option value="1">1 page (~240 items)</option>
    <option value="3">3 pages (~720 items)</option>
    <option value="5">5 pages (~1,200 items)</option>
    <option value="10">10 pages (~2,400 items)</option>
    <option value="custom">Custom…</option>
  </select>
</div>
<div class="mb-3 d-none" id="customPagesWrap">
  <label class="form-label fw-semibold">Custom page count</label>
  <input name="pages_custom" type="number" class="form-control" value="2" min="1" max="100" />
</div>
<script>
function toggleCustomPages(sel) {
  document.getElementById('customPagesWrap').classList.toggle('d-none', sel.value !== 'custom');
}
</script>
"""

@app.route("/scrape/url", methods=["GET", "POST"])
def scrape_url():
    if request.method == "POST":
        if _JOB["running"]:
            flash("A scrape is already in progress — stop it first or wait.", "error")
            return redirect(url_for("scrape_progress_page"))
        url        = request.form.get("url", "").strip()
        store_name = request.form.get("store_name", "").strip() or None
        pages      = _parse_pages(request.form)
        if not url:
            flash("Please enter a URL.", "error")
            return redirect(url_for("scrape_url"))
        _jset(running=True, done=False, task="URL Scrape", detail=url,
              page=0, collected=0, saved=0, log=[], error=None, stop_requested=False)
        threading.Thread(target=_run_url_scrape, args=(url, pages, store_name), daemon=True).start()
        return redirect(url_for("scrape_progress_page"))

    return render_template(
        "scrape.html",
        form_title="Scrape from URL",
        form_desc="Paste any eBay search or browse URL — sold/active status is detected automatically from the URL.",
        card_class="url-card",
        form_fields=_URL_FIELDS,
        tips=[
            "Works with any eBay search URL — just copy from your browser.",
            "Sold/active status is auto-detected from URL parameters (LH_Sold, LH_Complete).",
            "You can pre-filter on eBay (condition, price, location) before copying the URL.",
            "'All pages (auto)' keeps going until eBay runs out of results.",
            "Each page returns ~240 listings.",
        ],
    )


# ---------------------------------------------------------------------------
# Scrape — Progress / Stop / Status
# ---------------------------------------------------------------------------

@app.route("/scrape/progress")
def scrape_progress_page():
    with _JOB_LOCK:
        status = dict(_JOB)
    return render_template("scrape_progress.html", status=status)


@app.route("/scrape/status")
def scrape_status_api():
    with _JOB_LOCK:
        return jsonify(dict(_JOB))


@app.route("/scrape/stop", methods=["POST"])
def scrape_stop():
    sc.request_stop()
    _jlog("Stop requested by user...")
    _jset(stop_requested=True)
    # Accept both AJAX (fetch) and plain form POST
    if request.headers.get("Accept", "").startswith("application/json") or \
       request.content_type == "application/json":
        return jsonify({"ok": True})
    return redirect(url_for("scrape_progress_page"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_pages(form) -> int:
    """Return page count from form. 0 means auto-paginate all."""
    val = form.get("pages", "0")
    if val == "custom":
        try:
            return max(1, min(100, int(form.get("pages_custom", 1) or 1)))
        except ValueError:
            return 1
    try:
        return max(0, int(val))
    except ValueError:
        return 0


def _bulk_save(listings: list[dict], store_name: str = None) -> int:
    db.init_db()
    saved = 0
    for item in listings:
        if sc._stop_requested:
            print("  Stop requested — halting save mid-batch.")
            break
        try:
            item["store_name"] = store_name
            db.upsert_listing(item)
            saved += 1
        except Exception as exc:
            print(f"  DB error: {exc}")
    return saved


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Starting eBay Scraper web UI at http://127.0.0.1:5000")
    app.run(debug=False, port=5000, threaded=True)
