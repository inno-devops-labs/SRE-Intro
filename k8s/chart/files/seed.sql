-- QuickTicket seed data
-- Lab 12 Bonus §12.14 — the date column is `scheduled_at`, not `event_date`.
-- This file bootstraps a *fresh* database (docker-entrypoint-initdb.d / a new
-- PV), so it has to describe the schema as it exists AFTER the expand-and-
-- contract rename, otherwise a freshly created cluster would boot the events
-- service against a column it no longer selects. The three rename revisions in
-- migrations/versions/ are guarded (IF EXISTS / column introspection) so that
-- `alembic upgrade head` is still a clean no-op on a database seeded from here.

CREATE TABLE IF NOT EXISTS events (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    venue TEXT NOT NULL,
    scheduled_at TIMESTAMPTZ NOT NULL,
    total_tickets INT NOT NULL,
    price_cents INT NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    id TEXT PRIMARY KEY,
    event_id INT REFERENCES events(id),
    quantity INT NOT NULL,
    total_cents INT NOT NULL,
    payment_ref TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'confirmed',
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Seed events
INSERT INTO events (name, venue, scheduled_at, total_tickets, price_cents) VALUES
    ('Go Conference 2026', 'Main Hall A', '2026-09-15 09:00:00+00', 100, 5000),
    ('SRE Meetup', 'Room 204', '2026-10-01 18:00:00+00', 30, 0),
    ('Cloud Native Summit', 'Expo Center', '2026-11-20 10:00:00+00', 500, 15000),
    ('Python Workshop', 'Lab 301', '2026-09-22 14:00:00+00', 25, 2000),
    ('Kubernetes Deep Dive', 'Auditorium B', '2026-10-10 10:00:00+00', 80, 8000)
ON CONFLICT DO NOTHING;
