import json
import os

from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

_SUPABASE_URL = os.environ["SUPABASE_URL"]
_SUPABASE_KEY = os.environ["SUPABASE_KEY"]


def _db() -> Client:
    return create_client(_SUPABASE_URL, _SUPABASE_KEY)


# Active listings live in `listings`; sold/completed listings live in `sold`.
# Both tables share the same column layout.
ACTIVE_TABLE = "listings"
SOLD_TABLE = "sold"


def _table_for(is_sold) -> str:
    return SOLD_TABLE if is_sold else ACTIVE_TABLE


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def init_db():
    """Verify the Supabase connection and that both tables exist."""
    try:
        _db().table(ACTIVE_TABLE).select("id").limit(1).execute()
        _db().table(SOLD_TABLE).select("id").limit(1).execute()
    except Exception as exc:
        raise RuntimeError(
            f"Supabase connection failed: {exc}\n"
            "Make sure you have run schema.sql in your Supabase SQL Editor "
            "(including the `sold` table)."
        )


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

def upsert_listing(data: dict) -> int:
    row = {
        "ebay_id":    data.get("ebay_id"),
        "url":        data["url"],
        "title":      data["title"],
        "price":      data.get("price"),
        "currency":   data.get("currency", "USD"),
        "condition":  data.get("condition"),
        "image_url":  data.get("image_url"),
        "seller":     data.get("seller"),
        "store_name": data.get("store_name"),
        "location":   data.get("location"),
        "shipping":   data.get("shipping"),
        "bids":       data.get("bids"),
        "is_sold":      bool(data.get("is_sold")),
        "status":       data.get("status") or ("Sold" if data.get("is_sold") else "Active"),
        "sold_date":    data.get("sold_date"),
        "listed_date":  data.get("listed_date"),
        "make":         data.get("make"),
        "model":      data.get("model"),
        "scraped_at": data.get("scraped_at"),
    }
    table = _table_for(data.get("is_sold"))
    result = _db().table(table).upsert(row, on_conflict="url").execute()
    return result.data[0]["id"]


def upsert_product_detail(data: dict):
    specifics = data.get("item_specifics")
    if isinstance(specifics, str):
        try:
            specifics = json.loads(specifics)
        except (json.JSONDecodeError, TypeError):
            specifics = {}

    row = {
        "listing_id":              data["listing_id"],
        "description":             data.get("description"),
        "item_specifics":          specifics or {},
        "buy_it_now_price":        data.get("buy_it_now_price"),
        "auction_end_time":        data.get("auction_end_time"),
        "seller_feedback_score":   data.get("seller_feedback_score"),
        "seller_feedback_percent": data.get("seller_feedback_percent"),
        "scraped_at":              data.get("scraped_at"),
    }
    _db().table("product_details").upsert(row, on_conflict="listing_id").execute()


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def get_stats() -> dict:
    sb = _db()
    active = sb.table(ACTIVE_TABLE).select("id", count="exact").execute().count or 0
    sold   = sb.table(SOLD_TABLE).select("id", count="exact").execute().count or 0
    seller_rows = (sb.table(ACTIVE_TABLE).select("seller").execute().data
                   + sb.table(SOLD_TABLE).select("seller").execute().data)
    sellers = len({r["seller"] for r in seller_rows if r.get("seller")})
    return {"total": active + sold, "sold": sold, "active": active, "sellers": sellers}


def get_recent_listings(limit: int = 10) -> list[dict]:
    sb = _db()
    rows = (sb.table(ACTIVE_TABLE).select("*").order("scraped_at", desc=True).limit(limit).execute().data
            + sb.table(SOLD_TABLE).select("*").order("scraped_at", desc=True).limit(limit).execute().data)
    rows.sort(key=lambda r: r.get("scraped_at") or "", reverse=True)
    return rows[:limit]


def _query_one_table(table: str, *, keyword, seller, store_name, make, model, limit):
    q = _db().table(table).select("*", count="exact")
    if keyword:
        q = q.ilike("title", f"%{keyword}%")
    if seller:
        q = q.ilike("seller", f"%{seller}%")
    if store_name:
        q = q.ilike("store_name", f"%{store_name}%")
    if make:
        q = q.ilike("make", f"%{make}%")
    if model:
        q = q.ilike("model", f"%{model}%")
    result = q.order("scraped_at", desc=True).limit(limit).execute()
    return result.data, (result.count or 0)


def query_listings(
    keyword: str = None,
    seller: str = None,
    store_name: str = None,
    make: str = None,
    model: str = None,
    sold_only: bool = False,
    active_only: bool = False,
    limit: int = 50,
) -> tuple[list[dict], int]:
    filters = dict(keyword=keyword, seller=seller, store_name=store_name,
                   make=make, model=model, limit=limit)

    if sold_only:
        return _query_one_table(SOLD_TABLE, **filters)
    if active_only:
        return _query_one_table(ACTIVE_TABLE, **filters)

    # "All" — merge both tables, sort by scraped_at, cap at limit.
    active_rows, active_total = _query_one_table(ACTIVE_TABLE, **filters)
    sold_rows, sold_total = _query_one_table(SOLD_TABLE, **filters)
    rows = active_rows + sold_rows
    rows.sort(key=lambda r: r.get("scraped_at") or "", reverse=True)
    return rows[:limit], (active_total + sold_total)


def get_listing_by_id(listing_id: int, source: str = None) -> dict | None:
    # `source` ("listings"/"sold") disambiguates the two id sequences; if
    # omitted we check active first, then sold.
    tables = [source] if source in (ACTIVE_TABLE, SOLD_TABLE) else [ACTIVE_TABLE, SOLD_TABLE]
    for table in tables:
        result = _db().table(table).select("*").eq("id", listing_id).execute()
        if result.data:
            return result.data[0]
    return None


def query_product_detail(listing_id: int) -> dict | None:
    result = _db().table("product_details").select("*").eq("listing_id", listing_id).execute()
    return result.data[0] if result.data else None
