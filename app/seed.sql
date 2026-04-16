-- QuickTicket seed data

CREATE TABLE IF NOT EXISTS events (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    venue TEXT NOT NULL,
    event_date TIMESTAMPTZ NOT NULL,
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
INSERT INTO events (name, venue, event_date, total_tickets, price_cents) VALUES
    ('Go Conference 2026', 'Main Hall A', '2026-09-15 09:00:00+00', 100, 5000),
    ('SRE Meetup', 'Room 204', '2026-10-01 18:00:00+00', 30, 0),
    ('Cloud Native Summit', 'Expo Center', '2026-11-20 10:00:00+00', 500, 15000),
    ('Python Workshop', 'Lab 301', '2026-09-22 14:00:00+00', 25, 2000),
    ('Kubernetes Deep Dive', 'Auditorium B', '2026-10-10 10:00:00+00', 80, 8000)
ON CONFLICT DO NOTHING;
