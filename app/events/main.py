"""QuickTicket Events API, compatible with the final scheduled_at schema."""
import json
import logging
import os
import re
import time
import uuid
from contextlib import contextmanager

import psycopg2.pool
import redis
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "postgres"),
    "port": int(os.getenv("DB_PORT", "5432")),
    "dbname": os.getenv("DB_NAME", "quickticket"),
    "user": os.getenv("DB_USER", "quickticket"),
    "password": os.getenv("DB_PASS", "quickticket"),
}
DB_MAX_CONNS = int(os.getenv("DB_MAX_CONNS", "10"))
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_TIMEOUT_MS = int(os.getenv("REDIS_TIMEOUT_MS", "1000"))
RESERVATION_TTL = int(os.getenv("RESERVATION_TTL", "300"))

logging.basicConfig(level=logging.INFO, format='{"level":"%(levelname)s","service":"events","msg":"%(message)s"}')
logger = logging.getLogger("events")
app = FastAPI(title="QuickTicket Events", version="1.1.0")
REQUEST_COUNT = Counter("events_requests_total", "Request count", ["method", "path", "status"])
REQUEST_DURATION = Histogram("events_request_duration_seconds", "Request duration", ["method", "path"])
RESERVATIONS_ACTIVE = Gauge("events_reservations_active", "Reservations held")
ORDERS_TOTAL = Counter("events_orders_total", "Confirmed orders")
DB_POOL_SIZE = Gauge("events_db_pool_size", "Checked-out database connections")

db_pool = None
redis_client = None
_redis_ok = False
_redis_checked_at = 0.0


@app.on_event("startup")
def startup():
    global db_pool, redis_client
    for attempt in range(10):
        try:
            db_pool = psycopg2.pool.ThreadedConnectionPool(minconn=2, maxconn=DB_MAX_CONNS, **DB_CONFIG)
            logger.info("Database connection pool ready on attempt %d", attempt + 1)
            break
        except Exception as error:
            logger.warning("Database not ready on attempt %d: %s", attempt + 1, error)
            time.sleep(2)
    try:
        redis_client = redis.Redis(
            host=REDIS_HOST, port=REDIS_PORT, socket_timeout=REDIS_TIMEOUT_MS / 1000,
            socket_connect_timeout=0.5, decode_responses=True,
        )
        redis_client.ping()
    except Exception as error:
        logger.warning("Redis unavailable at startup: %s", error)
        redis_client = None


@contextmanager
def connection():
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    conn = db_pool.getconn()
    try:
        yield conn
    finally:
        db_pool.putconn(conn)


def _redis_healthy():
    global _redis_ok, _redis_checked_at
    now = time.monotonic()
    if now - _redis_checked_at < 4:
        return _redis_ok
    _redis_checked_at = now
    try:
        _redis_ok = bool(redis_client and redis_client.ping())
    except Exception:
        _redis_ok = False
    return _redis_ok


def _normalize_path(path):
    path = re.sub(r"/events/\d+", "/events/{id}", path)
    return re.sub(r"/reservations/[a-f0-9-]+", "/reservations/{id}", path)


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    started = time.monotonic()
    response = await call_next(request)
    if request.url.path != "/metrics":
        path = _normalize_path(request.url.path)
        REQUEST_COUNT.labels(request.method, path, str(response.status_code)).inc()
        REQUEST_DURATION.labels(request.method, path).observe(time.monotonic() - started)
    return response


@app.get("/health")
def health():
    postgres_healthy = False
    if db_pool is not None:
        try:
            with connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    postgres_healthy = cursor.fetchone()[0] == 1
        except Exception:
            postgres_healthy = False
    redis_healthy = _redis_healthy()
    healthy = postgres_healthy and redis_healthy
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={
            "status": "healthy" if healthy else "degraded",
            "checks": {"postgres": "ok" if postgres_healthy else "down", "redis": "ok" if redis_healthy else "down"},
        },
    )


@app.get("/metrics")
def metrics():
    if db_pool is not None:
        DB_POOL_SIZE.set(len(db_pool._used))
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


def _event_record(row):
    return {
        "id": row[0], "name": row[1], "venue": row[2], "date": row[3].isoformat(),
        "total_tickets": row[4], "price_cents": row[5],
        "available": max(0, row[4] - row[6]),
    }


@app.get("/events")
def list_events():
    with connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT e.id, e.name, e.venue, e.scheduled_at AS event_date,
                       e.total_tickets, e.price_cents, COALESCE(SUM(o.quantity), 0) AS confirmed
                FROM events AS e LEFT JOIN orders AS o ON e.id = o.event_id
                GROUP BY e.id ORDER BY e.scheduled_at
            """)
            return [_event_record(row) for row in cursor.fetchall()]


@app.get("/events/{event_id}")
def get_event(event_id: int):
    with connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT e.id, e.name, e.venue, e.scheduled_at AS event_date,
                       e.total_tickets, e.price_cents, COALESCE(SUM(o.quantity), 0) AS confirmed
                FROM events AS e LEFT JOIN orders AS o ON e.id = o.event_id
                WHERE e.id = %s GROUP BY e.id
            """, (event_id,))
            row = cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return _event_record(row)


def _available(event_id, total_tickets):
    with connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT COALESCE(SUM(quantity), 0) FROM orders WHERE event_id=%s", (event_id,))
            confirmed = cursor.fetchone()[0]
    held = 0
    if redis_client is not None:
        try:
            held = int(redis_client.get(f"event:{event_id}:held") or 0)
        except Exception as error:
            logger.warning("Could not read hold counter: %s", error)
    return max(0, total_tickets - confirmed - held)


@app.post("/events/{event_id}/reserve")
def reserve_tickets(event_id: int, request_body: dict = None):
    if not request_body:
        raise HTTPException(status_code=400, detail="Request body required")
    quantity = request_body.get("quantity", 1)
    if not isinstance(quantity, int) or quantity not in range(1, 11):
        raise HTTPException(status_code=400, detail="Quantity must be 1-10")
    with connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT total_tickets, price_cents FROM events WHERE id = %s", (event_id,))
            row = cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Event not found")
    if _available(event_id, row[0]) < quantity:
        raise HTTPException(status_code=409, detail="Not enough tickets")
    if redis_client is None:
        raise HTTPException(status_code=503, detail="Reservation store unavailable")
    reservation_id = str(uuid.uuid4())
    payload = {
        "event_id": event_id, "quantity": quantity,
        "total_cents": row[1] * quantity, "created_at": time.time(),
    }
    try:
        redis_client.setex(f"reservation:{reservation_id}", RESERVATION_TTL, json.dumps(payload))
        redis_client.incrby(f"event:{event_id}:held", quantity)
        RESERVATIONS_ACTIVE.inc()
    except Exception as error:
        logger.warning("Reservation write failed: %s", error)
        raise HTTPException(status_code=503, detail="Reservation store unavailable")
    return {"reservation_id": reservation_id, **{k: payload[k] for k in ("event_id", "quantity", "total_cents")}, "expires_in_seconds": RESERVATION_TTL}


@app.post("/reservations/{reservation_id}/confirm")
def confirm_reservation(reservation_id: str, request_body: dict = None):
    payment_ref = (request_body or {}).get("payment_ref", "unknown")
    try:
        raw = redis_client.get(f"reservation:{reservation_id}") if redis_client else None
    except Exception:
        raise HTTPException(status_code=503, detail="Reservation store unavailable")
    if not raw:
        raise HTTPException(status_code=404, detail="Reservation not found or expired")
    reservation = json.loads(raw)
    with connection() as conn:
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO orders (id, event_id, quantity, total_cents, payment_ref) VALUES (%s, %s, %s, %s, %s)",
                    (reservation_id, reservation["event_id"], reservation["quantity"], reservation["total_cents"], payment_ref),
                )
            conn.commit()
        except Exception as error:
            conn.rollback()
            logger.error("Order confirmation failed: %s", error)
            raise HTTPException(status_code=500, detail="Order confirmation failed")
    try:
        redis_client.delete(f"reservation:{reservation_id}")
        redis_client.decrby(f"event:{reservation['event_id']}:held", reservation["quantity"])
    except Exception as error:
        logger.warning("Could not release hold counter: %s", error)
    RESERVATIONS_ACTIVE.dec()
    ORDERS_TOTAL.inc()
    return {
        "order_id": reservation_id, "event_id": reservation["event_id"],
        "quantity": reservation["quantity"], "total_cents": reservation["total_cents"],
        "status": "confirmed",
    }
