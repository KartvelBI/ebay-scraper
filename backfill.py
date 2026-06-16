"""
One-time script: reads every listing from Supabase, extracts car make/model
from the title, and writes the result back.

Usage:
    python backfill.py              # skip rows that already have make/model set
    python backfill.py --overwrite  # re-extract and overwrite ALL rows
"""
import os
import sys

from dotenv import load_dotenv
from supabase import create_client

from make_model_utils import extract_make_model

load_dotenv()

sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])

PAGE = 1000


def run(overwrite: bool = False) -> None:
    updated = skipped = no_match = 0
    offset = 0

    print("Starting make/model backfill...\n")

    while True:
        rows = (
            sb.table("listings")
            .select("id,title,make,model")
            .range(offset, offset + PAGE - 1)
            .execute()
            .data
        )
        if not rows:
            break

        for row in rows:
            # Skip if already set and not overwriting
            if not overwrite and (row.get("make") or row.get("model")):
                skipped += 1
                continue

            make, model = extract_make_model(row.get("title") or "")
            if not make and not model:
                no_match += 1
                continue

            sb.table("listings").update({"make": make, "model": model}).eq("id", row["id"]).execute()
            updated += 1
            title_short = (row.get("title") or "")[:60]
            print(f"  [{row['id']:>6}] {title_short:<60}  =>  {make or '?'} / {model or '?'}")

        if len(rows) < PAGE:
            break
        offset += PAGE

    print(f"\nDone. Updated: {updated} | Skipped (already set): {skipped} | No match: {no_match}")


if __name__ == "__main__":
    run(overwrite="--overwrite" in sys.argv)
