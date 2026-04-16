#!/usr/bin/env bash
# QuickTicket load generator — sends a mix of read and write traffic.
# Usage: ./loadgen/run.sh [requests_per_second] [duration_seconds]

set -euo pipefail

GATEWAY="${GATEWAY_URL:-http://localhost:3080}"
RPS="${1:-5}"
DURATION="${2:-60}"
INTERVAL=$(echo "scale=4; 1/$RPS" | bc)

echo "QuickTicket Load Generator"
echo "Target: $GATEWAY | RPS: $RPS | Duration: ${DURATION}s"
echo "---"

SUCCESS=0
FAIL=0
START=$(date +%s)

while true; do
    NOW=$(date +%s)
    ELAPSED=$((NOW - START))
    if [ "$ELAPSED" -ge "$DURATION" ]; then
        break
    fi

    # 70% reads, 20% reserves, 10% full purchase flow
    RAND=$((RANDOM % 100))

    if [ "$RAND" -lt 70 ]; then
        # List events
        STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$GATEWAY/events" 2>/dev/null || echo "000")
    elif [ "$RAND" -lt 90 ]; then
        # Reserve tickets for a random event (1-5)
        EVENT_ID=$((RANDOM % 5 + 1))
        STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST \
            -H "Content-Type: application/json" \
            -d "{\"quantity\": 1}" \
            "$GATEWAY/events/$EVENT_ID/reserve" 2>/dev/null || echo "000")
    else
        # Full purchase flow: reserve + pay
        EVENT_ID=$((RANDOM % 5 + 1))
        RESERVE_RESP=$(curl -s -X POST \
            -H "Content-Type: application/json" \
            -d "{\"quantity\": 1}" \
            "$GATEWAY/events/$EVENT_ID/reserve" 2>/dev/null || echo "{}")
        RES_ID=$(echo "$RESERVE_RESP" | grep -o '"reservation_id":"[^"]*"' | cut -d'"' -f4 || echo "")
        if [ -n "$RES_ID" ]; then
            STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST \
                "$GATEWAY/reserve/$RES_ID/pay" 2>/dev/null || echo "000")
        else
            STATUS="409"
        fi
    fi

    if [ "$STATUS" -ge 200 ] && [ "$STATUS" -lt 400 ]; then
        SUCCESS=$((SUCCESS + 1))
    else
        FAIL=$((FAIL + 1))
    fi

    # Progress every 10 seconds
    if [ $((ELAPSED % 10)) -eq 0 ] && [ "$ELAPSED" -gt 0 ]; then
        TOTAL=$((SUCCESS + FAIL))
        if [ "$TOTAL" -gt 0 ]; then
            ERROR_RATE=$(echo "scale=1; $FAIL * 100 / $TOTAL" | bc)
            echo "[${ELAPSED}s] requests=$TOTAL success=$SUCCESS fail=$FAIL error_rate=${ERROR_RATE}%"
        fi
    fi

    sleep "$INTERVAL" 2>/dev/null || true
done

TOTAL=$((SUCCESS + FAIL))
if [ "$TOTAL" -gt 0 ]; then
    ERROR_RATE=$(echo "scale=1; $FAIL * 100 / $TOTAL" | bc)
else
    ERROR_RATE="0"
fi

echo "---"
echo "Done. total=$TOTAL success=$SUCCESS fail=$FAIL error_rate=${ERROR_RATE}%"
