-- Run this once in your Supabase project: Dashboard → SQL Editor → New query

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
    location    TEXT,
    shipping    TEXT,
    bids        INTEGER,
    is_sold     BOOLEAN DEFAULT FALSE,
    sold_date   TEXT,
    listed_date TEXT,
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

-- Migration: run if the table already exists
-- ALTER TABLE listings ADD COLUMN IF NOT EXISTS listed_date TEXT;

CREATE INDEX IF NOT EXISTS idx_listings_ebay_id ON listings(ebay_id);
CREATE INDEX IF NOT EXISTS idx_listings_seller  ON listings(seller);
CREATE INDEX IF NOT EXISTS idx_listings_is_sold ON listings(is_sold);
CREATE INDEX IF NOT EXISTS idx_listings_scraped ON listings(scraped_at DESC);
