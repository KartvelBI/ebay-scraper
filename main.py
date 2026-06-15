"""
eBay Scraper — CLI entry point

Usage examples:
  python main.py search "vintage rolex" --pages 3 --sold
  python main.py seller "best_seller_123" --pages 5
  python main.py product "https://www.ebay.com/itm/123456789"
  python main.py query --keyword "rolex" --sold --limit 20
"""

import argparse
import json
import sys

import database as db
import scraper


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------

def cmd_search(args):
    db.init_db()
    print(f'Searching eBay for "{args.keyword}" — {args.pages} page(s), sold={args.sold}')
    listings = scraper.scrape_search(args.keyword, pages=args.pages, sold_only=args.sold)

    saved = 0
    for item in listings:
        try:
            db.upsert_listing(item)
            saved += 1
        except Exception as exc:
            print(f"  DB error for {item.get('url')}: {exc}")

    print(f"\nDone. {saved}/{len(listings)} listings saved to {db.DB_PATH}.")


def cmd_seller(args):
    db.init_db()
    print(f'Scraping seller "{args.seller}" — {args.pages} page(s)')
    listings = scraper.scrape_seller(args.seller, pages=args.pages)

    saved = 0
    for item in listings:
        try:
            db.upsert_listing(item)
            saved += 1
        except Exception as exc:
            print(f"  DB error for {item.get('url')}: {exc}")

    print(f"\nDone. {saved}/{len(listings)} listings saved to {db.DB_PATH}.")


def cmd_product(args):
    db.init_db()
    print(f"Scraping product: {args.url}")
    listing, detail = scraper.scrape_product(args.url)

    if not listing:
        print("Failed to scrape the product page.")
        sys.exit(1)

    listing_id = db.upsert_listing(listing)
    print(f"  Listing saved (id={listing_id}): {listing['title'][:60]}")

    if detail:
        detail["listing_id"] = listing_id
        db.upsert_product_detail(detail)
        specifics = json.loads(detail["item_specifics"] or "{}")
        print(f"  Details saved. Item specifics: {len(specifics)} fields.")
        if specifics:
            for k, v in list(specifics.items())[:8]:
                print(f"    {k}: {v}")
            if len(specifics) > 8:
                print(f"    … and {len(specifics) - 8} more.")

    print(f"\nDone. Data saved to {db.DB_PATH}.")


def cmd_query(args):
    db.init_db()
    rows = db.query_listings(
        keyword=args.keyword,
        seller=args.seller,
        sold_only=args.sold,
        limit=args.limit,
    )

    if not rows:
        print("No results found.")
        return

    col_w = {"title": 45, "price": 10, "cond": 15, "seller": 18, "sold": 4}
    header = (
        f"{'#':<4} "
        f"{'Title':<{col_w['title']}} "
        f"{'Price':>{col_w['price']}} "
        f"{'Condition':<{col_w['cond']}} "
        f"{'Seller':<{col_w['seller']}} "
        f"{'Sold':<{col_w['sold']}}"
    )
    print(header)
    print("-" * len(header))

    for i, row in enumerate(rows, 1):
        title = (row["title"] or "")[:col_w["title"]]
        price = f"${row['price']:.2f}" if row["price"] else "N/A"
        cond  = (row["condition"] or "")[:col_w["cond"]]
        seller = (row["seller"] or "")[:col_w["seller"]]
        sold  = "Yes" if row["is_sold"] else "No"
        print(
            f"{i:<4} "
            f"{title:<{col_w['title']}} "
            f"{price:>{col_w['price']}} "
            f"{cond:<{col_w['cond']}} "
            f"{seller:<{col_w['seller']}} "
            f"{sold:<{col_w['sold']}}"
        )

    print(f"\n{len(rows)} row(s) returned from {db.DB_PATH}.")


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ebay_scraper",
        description="eBay scraper — stores results in a local SQLite database.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # search
    p_search = sub.add_parser("search", help="Search eBay listings by keyword.")
    p_search.add_argument("keyword", help='Search term, e.g. "vintage rolex"')
    p_search.add_argument("--pages", type=int, default=1, help="Number of result pages (default: 1)")
    p_search.add_argument("--sold", action="store_true", help="Filter to sold/completed listings only")
    p_search.set_defaults(func=cmd_search)

    # seller
    p_seller = sub.add_parser("seller", help="Scrape all active listings from a seller.")
    p_seller.add_argument("seller", help="eBay seller username")
    p_seller.add_argument("--pages", type=int, default=1, help="Number of pages (default: 1)")
    p_seller.set_defaults(func=cmd_seller)

    # product
    p_product = sub.add_parser("product", help="Scrape a single product page (full details).")
    p_product.add_argument("url", help="Full eBay listing URL")
    p_product.set_defaults(func=cmd_product)

    # query
    p_query = sub.add_parser("query", help="Query the local database.")
    p_query.add_argument("--keyword", help="Filter by title keyword")
    p_query.add_argument("--seller", help="Filter by seller name")
    p_query.add_argument("--sold", action="store_true", help="Show only sold items")
    p_query.add_argument("--limit", type=int, default=50, help="Max rows to return (default: 50)")
    p_query.set_defaults(func=cmd_query)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
