-- Only the dedicated synthetic event is changed; course inventory is untouched.
INSERT INTO events (id,name,venue,event_date,total_tickets,price_cents)
VALUES (7808,'Lab 8 synthetic checkout','Training cluster','2026-11-20 10:00:00+00',1000000,0)
ON CONFLICT (id) DO NOTHING;
