-- A dedicated synthetic event prevents ticket depletion from masking failures.
INSERT INTO events (id, name, venue, event_date, total_tickets, price_cents)
VALUES (6006, 'Lab 6 incident exercise', 'Test cluster', '2099-01-01', 1000000, 100)
ON CONFLICT (id) DO NOTHING;
