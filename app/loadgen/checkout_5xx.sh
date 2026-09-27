#!/usr/bin/env bash
# QuickTicket checkout-focused load generator for Lab 6 alert testing.
# Usage: ./loadgen/checkout_5xx.sh [requests_per_second] [duration_seconds] [event_id]

set -euo pipefail

GATEWAY="${GATEWAY_URL:-http://localhost:3080}"
RPS="${1:-5}"
DURATION="${2:-300}"
EVENT_ID="${3:-3}"
INTERVAL=$(awk "BEGIN { printf \"%.4f\", 1 / $RPS }")

echo "QuickTicket checkout-focused load generator"
echo "Target: $GATEWAY | RPS: $RPS | Duration: ${DURATION}s | Event: $EVENT_ID"
echo "This script reserves one ticket and immediately calls the payment endpoint."
echo "---"

SUCCESS=0
FAIL=0
RESERVE_FAIL=0
PAY_4XX=0
PAY_5XX=0
PAY_OTHER=0
START=$(date +%s)

while true; do
    NOW=$(date +%s)
    ELAPSED=$((NOW - START))
    if [ "$ELAPSED" -ge "$DURATION" ]; then
        break
    fi

    RESERVE_RESP=$(curl -s -X POST \
        -H "Content-Type: application/json" \
        -d '{"quantity": 1}' \
        "$GATEWAY/events/$EVENT_ID/reserve" 2>/dev/null || echo "{}")

    RES_ID=$(echo "$RESERVE_RESP" | grep -o '"reservation_id":"[^"]*"' | cut -d'"' -f4 || true)
    if [ -z "$RES_ID" ]; then
        STATUS="reserve_failed"
        RESERVE_FAIL=$((RESERVE_FAIL + 1))
        FAIL=$((FAIL + 1))
    else
        STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST \
            "$GATEWAY/reserve/$RES_ID/pay" 2>/dev/null || echo "000")

        if [ "$STATUS" -ge 200 ] && [ "$STATUS" -lt 400 ]; then
            SUCCESS=$((SUCCESS + 1))
        else
            FAIL=$((FAIL + 1))
            if [ "$STATUS" -ge 500 ] && [ "$STATUS" -lt 600 ]; then
                PAY_5XX=$((PAY_5XX + 1))
            elif [ "$STATUS" -ge 400 ] && [ "$STATUS" -lt 500 ]; then
                PAY_4XX=$((PAY_4XX + 1))
            else
                PAY_OTHER=$((PAY_OTHER + 1))
            fi
        fi
    fi

    if [ $((ELAPSED % 10)) -eq 0 ] && [ "$ELAPSED" -gt 0 ]; then
        TOTAL=$((SUCCESS + FAIL))
        if [ "$TOTAL" -gt 0 ]; then
            ERROR_RATE=$(awk "BEGIN { printf \"%.1f\", $FAIL * 100 / $TOTAL }")
            FIVE_XX_RATE=$(awk "BEGIN { printf \"%.1f\", $PAY_5XX * 100 / $TOTAL }")
            echo "[${ELAPSED}s] total=$TOTAL success=$SUCCESS fail=$FAIL 5xx=$PAY_5XX reserve_fail=$RESERVE_FAIL error_rate=${ERROR_RATE}% pay_5xx_rate=${FIVE_XX_RATE}%"
        fi
    fi

    sleep "$INTERVAL" 2>/dev/null || true
done

TOTAL=$((SUCCESS + FAIL))
if [ "$TOTAL" -gt 0 ]; then
    ERROR_RATE=$(awk "BEGIN { printf \"%.1f\", $FAIL * 100 / $TOTAL }")
    FIVE_XX_RATE=$(awk "BEGIN { printf \"%.1f\", $PAY_5XX * 100 / $TOTAL }")
else
    ERROR_RATE="0.0"
    FIVE_XX_RATE="0.0"
fi

echo "---"
echo "Done. total=$TOTAL success=$SUCCESS fail=$FAIL reserve_fail=$RESERVE_FAIL pay_4xx=$PAY_4XX pay_5xx=$PAY_5XX pay_other=$PAY_OTHER error_rate=${ERROR_RATE}% pay_5xx_rate=${FIVE_XX_RATE}%"
