import json
import os

from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

_SUPABASE_URL = os.environ["SUPABASE_URL"]
_SUPABASE_KEY = os.environ["SUPABASE_KEY"]


def _db() -> Client:
    return create_client(_SUPABASE_URL, _SUPABASE_KEY)


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def init_db():
    """Verify the Supabase connection and that tables exist."""
    try:
        _db().table("listings").select("id").limit(1).execute()
    except Exception as exc:
        raise RuntimeError(
            f"Supabase connection failed: {exc}\n"
            "Make sure you have run schema.sql in your Supabase SQL Editor."
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
        "sold_date":    data.get("sold_date"),
        "listed_date":  data.get("listed_date"),
        "make":         data.get("make"),
        "model":      data.get("model"),
        "scraped_at": data.get("scraped_at"),
    }
    result = _db().table("listings").upsert(row, on_conflict="url").execute()
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
    total  = sb.table("listings").select("id", count="exact").execute().count or 0
    sold   = sb.table("listings").select("id", count="exact").eq("is_sold", True).execute().count or 0
    active = sb.table("listings").select("id", count="exact").eq("is_sold", False).execute().count or 0
    res    = sb.table("listings").select("seller").execute()
    sellers = len({r["seller"] for r in res.data if r.get("seller")})
    return {"total": total, "sold": sold, "active": active, "sellers": sellers}


def get_recent_listings(limit: int = 10) -> list[dict]:
    result = _db().table("listings").select("*").order("scraped_at", desc=True).limit(limit).execute()
    return result.data


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
    sb = _db()
    q = sb.table("listings").select("*", count="exact")
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
    if sold_only:
        q = q.eq("is_sold", True)
    elif active_only:
        q = q.eq("is_sold", False)
    result = q.order("scraped_at", desc=True).limit(limit).execute()
    return result.data, (result.count or 0)


def get_listing_by_id(listing_id: int) -> dict | None:
    result = _db().table("listings").select("*").eq("id", listing_id).execute()
    return result.data[0] if result.data else None


def query_product_detail(listing_id: int) -> dict | None:
    result = _db().table("product_details").select("*").eq("listing_id", listing_id).execute()
    return result.data[0] if result.data else None
