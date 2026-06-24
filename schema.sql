-- Run this once in your Supabase project: Dashboard → SQL Editor → New query

-- Active listings live here; sold/completed listings live in `sold` (below).
CREATE TABLE IF NOT EXISTS listings (
    id          BIGSERIAL PRIMARY KEY,
    ebay_id     TEXT,
    url         TEXT NOT NULL UNIQUE,
    title       TEXT NOT NULL,
    price       NUMERIC,
    currency    TEXT DEFAULT 'USD',
    condition   TEXT,
    image_url   TEXT,
    seller      TEXT,
    store_name  TEXT,
    location    TEXT,
    shipping    TEXT,
    bids        INTEGER,
    is_sold     BOOLEAN DEFAULT FALSE,
    status      TEXT DEFAULT 'Active',
    sold_date   TEXT,
    listed_date TEXT,
    make        TEXT,
    model       TEXT,
    scraped_at  TEXT NOT NULL
);

-- Sold / completed listings. Same shape as `listings`, separate id sequence.
CREATE TABLE IF NOT EXISTS sold (
    id          BIGSERIAL PRIMARY KEY,
    ebay_id     TEXT,
    url         TEXT NOT NULL UNIQUE,
    title       TEXT NOT NULL,
    price       NUMERIC,
    currency    TEXT DEFAULT 'USD',
    condition   TEXT,
    image_url   TEXT,
    seller      TEXT,
    store_name  TEXT,
    location    TEXT,
    shipping    TEXT,
    bids        INTEGER,
    is_sold     BOOLEAN DEFAULT TRUE,
    status      TEXT DEFAULT 'Sold',
    sold_date   TEXT,
    listed_date TEXT,
    make        TEXT,
    model       TEXT,
    scraped_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS product_details (
    id                      BIGSERIAL PRIMARY KEY,
    listing_id              BIGINT NOT NULL UNIQUE REFERENCES listings(id) ON DELETE CASCADE,
    description             TEXT,
    item_specifics          JSONB,
    buy_it_now_price        NUMERIC,
    auction_end_time        TEXT,
    seller_feedback_score   INTEGER,
    seller_feedback_percent NUMERIC,
    scraped_at              TEXT NOT NULL
);

-- Daily scheduled scrapes
CREATE TABLE IF NOT EXISTS schedules (
    id          BIGSERIAL PRIMARY KEY,
    label       TEXT,
    scrape_type TEXT NOT NULL DEFAULT 'sold',   -- 'sold' | 'newly'
    url         TEXT NOT NULL,
    pages       INTEGER NOT NULL DEFAULT 0,      -- 0 = all pages
    store_name  TEXT,
    run_at      TEXT NOT NULL,                   -- 'HH:MM' (24h)
    enabled     BOOLEAN NOT NULL DEFAULT TRUE,
    last_run    TEXT,                            -- 'YYYY-MM-DD' of last fire
    created_at  TEXT
);

-- Migration: run if the tables already exist
-- ALTER TABLE listings ADD COLUMN IF NOT EXISTS listed_date TEXT;
-- ALTER TABLE listings ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'Active';
-- Backfill status from the existing is_sold flag for rows already stored:
-- UPDATE listings SET status = CASE WHEN is_sold THEN 'Sold' ELSE 'Active' END WHERE status IS NULL;
--
-- Sold table: create it (run the CREATE TABLE sold (...) above), then move any
-- sold rows that already accumulated in `listings` over to it:
-- INSERT INTO sold (ebay_id, url, title, price, currency, condition, image_url,
--                   seller, store_name, location, shipping, bids, is_sold, status,
--                   sold_date, listed_date, make, model, scraped_at)
--   SELECT ebay_id, url, title, price, currency, condition, image_url, seller,
--          store_name, location, shipping, bids, is_sold, status, sold_date,
--          listed_date, make, model, scraped_at
--   FROM listings WHERE is_sold
--   ON CONFLICT (url) DO NOTHING;
-- DELETE FROM listings WHERE is_sold;

CREATE INDEX IF NOT EXISTS idx_listings_ebay_id ON listings(ebay_id);
CREATE INDEX IF NOT EXISTS idx_listings_seller  ON listings(seller);
CREATE INDEX IF NOT EXISTS idx_listings_is_sold ON listings(is_sold);
CREATE INDEX IF NOT EXISTS idx_listings_status  ON listings(status);
CREATE INDEX IF NOT EXISTS idx_listings_scraped ON listings(scraped_at DESC);

CREATE INDEX IF NOT EXISTS idx_sold_ebay_id ON sold(ebay_id);
CREATE INDEX IF NOT EXISTS idx_sold_seller  ON sold(seller);
CREATE INDEX IF NOT EXISTS idx_sold_scraped ON sold(scraped_at DESC);
