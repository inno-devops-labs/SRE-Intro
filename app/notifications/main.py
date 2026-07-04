from fastapi import FastAPI
import os
import time
import random
import asyncio

app = FastAPI()

NOTIFY_FAILURE_RATE = float(os.getenv("NOTIFY_FAILURE_RATE", "0.0"))
NOTIFY_LATENCY_MS = int(os.getenv("NOTIFY_LATENCY_MS", "0"))

@app.post("/notify")
async def notify(payload: dict):
    start = time.time()
    try:
        if random.random() < NOTIFY_FAILURE_RATE:
            raise Exception("Simulated notify failure")
        if NOTIFY_LATENCY_MS > 0:
            await asyncio.sleep(NOTIFY_LATENCY_MS / 1000.0)
        return {"status": "sent", "order_id": payload.get("order_id")}
    except Exception:
        print("Notify failed")
        raise
    finally:
        print(f"Notify took {time.time() - start:.3f}s")

@app.get("/health")
async def health():
    return {"status": "healthy", "failure_rate": NOTIFY_FAILURE_RATE, "latency_ms": NOTIFY_LATENCY_MS}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8083)
