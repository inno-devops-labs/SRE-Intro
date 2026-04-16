"""QuickTicket Events — Ticket management, reservations, orders."""

import os
import uuid
import time
import json
import logging

import psycopg2
import psycopg2.pool
import redis
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST

# --- Config ---
DB_HOST = os.getenv("DB_HOST", "postgres")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "quickticket")
DB_USER = os.getenv("DB_USER", "quickticket")
DB_PASS = os.getenv("DB_PASS", "quickticket")
DB_MAX_CONNS = int(os.getenv("DB_MAX_CONNS", "10"))

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_TIMEOUT_MS = int(os.getenv("REDIS_TIMEOUT_MS", "1000"))
RESERVATION_TTL = int(os.getenv("RESERVATION_TTL", "300"))  # 5 minutes

# --- Logging ---
logging.basicConfig(
    format='{"time":"%(asctime)s","level":"%(levelname)s","service":"events","msg":"%(message)s"}',
    level=logging.INFO,
)
log = logging.getLogger("events")

# --- App ---
app = FastAPI(title="QuickTicket Events", version="1.0.0")

# --- Prometheus metrics ---
REQUEST_COUNT = Counter("events_requests_total", "Total requests", ["method", "path", "status"])
REQUEST_DURATION = Histogram("events_request_duration_seconds", "Request duration", ["method", "path"])
RESERVATIONS_ACTIVE = Gauge("events_reservations_active", "Active reservations in Redis")
ORDERS_TOTAL = Counter("events_orders_total", "Total confirmed orders")
DB_POOL_SIZE = Gauge("events_db_pool_size", "Current DB connection pool size")

# --- DB pool ---
db_pool = None
redis_client = None


@app.on_event("startup")
def startup():
    global db_pool, redis_client
    for attempt in range(10):
        try:
            db_pool = psycopg2.pool.ThreadedConnectionPool(
                minconn=2, maxconn=DB_MAX_CONNS,
                host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASS,
            )
            log.info(f"DB pool created (max={DB_MAX_CONNS})")
            break
        except Exception as e:
            log.warning(f"DB connection attempt {attempt+1}/10 failed: {e}")
            time.sleep(2)
    else:
        log.error("Could not connect to database after 10 attempts")

    try:
        redis_client = redis.Redis(
            host=REDIS_HOST, port=REDIS_PORT,
            socket_timeout=REDIS_TIMEOUT_MS / 1000,
            socket_connect_timeout=0.5,
            decode_responses=True,
        )
        redis_client.ping()
        log.info("Redis connected")
    except Exception as e:
        log.warning(f"Redis connection failed: {e}")
        redis_client = None


def get_db():
    conn = db_pool.getconn()
    try:
        yield conn
    finally:
        db_pool.putconn(conn)


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    duration = time.time() - start
    path = request.url.path
    if not path.startswith("/metrics"):
        REQUEST_COUNT.labels(request.method, path, response.status_code).inc()
        REQUEST_DURATION.labels(request.method, path).observe(duration)
    return response


@app.get("/health")
def health():
    checks = {}
    try:
        conn = db_pool.getconn()
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.close()
        db_pool.putconn(conn)
        checks["postgres"] = "ok"
    except Exception:
        checks["postgres"] = "down"
    checks["redis"] = "ok" if _check_redis() else "down"

    all_ok = all(v == "ok" for v in checks.values())
    return JSONResponse(
        status_code=200 if all_ok else 503,
        content={"status": "healthy" if all_ok else "degraded", "checks": checks},
    )


@app.get("/metrics")
def metrics():
    from starlette.responses import Response
    if db_pool:
        DB_POOL_SIZE.set(len(db_pool._used))
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/events")
def list_events():
    conn = db_pool.getconn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT e.id, e.name, e.venue, e.event_date, e.total_tickets, e.price_cents,
                   COALESCE(SUM(o.quantity), 0) as confirmed
            FROM events e LEFT JOIN orders o ON e.id = o.event_id
            GROUP BY e.id ORDER BY e.event_date
        """)
        rows = cur.fetchall()
        cur.close()
        return [
            {
                "id": r[0], "name": r[1], "venue": r[2],
                "date": r[3].isoformat(), "total_tickets": r[4],
                "price_cents": r[5], "available": max(0, r[4] - r[6]),
            }
            for r in rows
        ]
    finally:
        db_pool.putconn(conn)


@app.get("/events/{event_id}")
def get_event(event_id: int):
    conn = db_pool.getconn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT e.id, e.name, e.venue, e.event_date, e.total_tickets, e.price_cents,
                   COALESCE(SUM(o.quantity), 0) as confirmed
            FROM events e LEFT JOIN orders o ON e.id = o.event_id
            WHERE e.id = %s GROUP BY e.id
        """, (event_id,))
        r = cur.fetchone()
        cur.close()
        if not r:
            raise HTTPException(404, "Event not found")
        return {
            "id": r[0], "name": r[1], "venue": r[2],
            "date": r[3].isoformat(), "total_tickets": r[4],
            "price_cents": r[5], "available": max(0, r[4] - r[6]),
        }
    finally:
        db_pool.putconn(conn)


@app.post("/events/{event_id}/reserve")
def reserve_tickets(event_id: int, request_body: dict = None):
    if not request_body:
        raise HTTPException(400, "Request body required")
    quantity = request_body.get("quantity", 1)
    if quantity < 1 or quantity > 10:
        raise HTTPException(400, "Quantity must be 1-10")

    conn = db_pool.getconn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT total_tickets, price_cents FROM events WHERE id=%s", (event_id,))
        row = cur.fetchone()
        cur.close()
        if not row:
            raise HTTPException(404, "Event not found")

        total_tickets, price_cents = row
        available = _get_available(event_id, total_tickets)
        if available < quantity:
            raise HTTPException(409, f"Not enough tickets (available: {available})")

        reservation_id = str(uuid.uuid4())
        reservation = {
            "event_id": event_id,
            "quantity": quantity,
            "total_cents": price_cents * quantity,
            "created_at": time.time(),
        }

        if redis_client:
            redis_client.setex(
                f"reservation:{reservation_id}",
                RESERVATION_TTL,
                json.dumps(reservation),
            )
            # Decrement available counter
            redis_client.decrby(f"event:{event_id}:held", -quantity)
            RESERVATIONS_ACTIVE.inc()
        else:
            log.warning("Redis unavailable — reservation not held")

        log.info(f"Reserved {quantity} tickets for event {event_id}: {reservation_id}")
        return {
            "reservation_id": reservation_id,
            "event_id": event_id,
            "quantity": quantity,
            "total_cents": price_cents * quantity,
            "expires_in_seconds": RESERVATION_TTL,
        }
    finally:
        db_pool.putconn(conn)


@app.post("/reservations/{reservation_id}/confirm")
def confirm_reservation(reservation_id: str, request_body: dict = None):
    payment_ref = (request_body or {}).get("payment_ref", "unknown")

    # Get reservation from Redis
    reservation = None
    if redis_client:
        raw = redis_client.get(f"reservation:{reservation_id}")
        if raw:
            reservation = json.loads(raw)

    if not reservation:
        raise HTTPException(404, "Reservation not found or expired")

    conn = db_pool.getconn()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO orders (id, event_id, quantity, total_cents, payment_ref) VALUES (%s, %s, %s, %s, %s)",
            (reservation_id, reservation["event_id"], reservation["quantity"],
             reservation["total_cents"], payment_ref),
        )
        conn.commit()
        cur.close()

        # Clean up reservation
        if redis_client:
            redis_client.delete(f"reservation:{reservation_id}")
            RESERVATIONS_ACTIVE.dec()
        ORDERS_TOTAL.inc()

        log.info(f"Order confirmed: {reservation_id}")
        return {
            "order_id": reservation_id,
            "event_id": reservation["event_id"],
            "quantity": reservation["quantity"],
            "total_cents": reservation["total_cents"],
            "status": "confirmed",
        }
    except Exception as e:
        conn.rollback()
        log.error(f"Order confirmation failed: {e}")
        raise HTTPException(500, "Order confirmation failed")
    finally:
        db_pool.putconn(conn)


def _get_available(event_id: int, total_tickets: int, redis_ok: bool = True) -> int:
    """Calculate available tickets = total - confirmed orders - held reservations."""
    conn = db_pool.getconn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COALESCE(SUM(quantity), 0) FROM orders WHERE event_id=%s", (event_id,))
        confirmed = cur.fetchone()[0]
        cur.close()
    finally:
        db_pool.putconn(conn)

    held = 0
    if redis_ok and redis_client:
        try:
            held = int(redis_client.get(f"event:{event_id}:held") or 0)
        except Exception as e:
            log.warning(f"Redis unavailable for availability check: {e}")

    return max(0, total_tickets - confirmed - held)


_redis_ok = True
_redis_checked_at = 0.0
_REDIS_CHECK_INTERVAL = 5.0  # seconds between Redis health checks


def _check_redis():
    """Check if Redis is reachable. Caches result for 5s to avoid DNS timeout blocking requests."""
    global _redis_ok, _redis_checked_at
    now = time.time()
    if now - _redis_checked_at < _REDIS_CHECK_INTERVAL:
        return _redis_ok
    _redis_checked_at = now
    if not redis_client:
        _redis_ok = False
        return False
    try:
        _redis_ok = redis_client.ping()
    except Exception:
        _redis_ok = False
    return _redis_ok
