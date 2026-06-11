#!/usr/bin/env bash
GATEWAY="http://localhost:3080"
DURATION=30
SUCCESS=0
FAIL=0
START=$(date +%s)

echo "Load test: Payment endpoint - 5 requests/sec for ${DURATION}s"
echo "Kill payments during this test to see error spike!"
echo "---"

# Create a reservation first
RESP=$(curl -s -X POST "$GATEWAY/events/1/reserve" \
    -H "Content-Type: application/json" \
    -d '{"quantity": 1}')
RES_ID=$(echo $RESP | grep -o '"reservation_id":"[^"]*"' | cut -d'"' -f4)
echo "Using reservation: $RES_ID"
echo ""

while [ $(($(date +%s) - START)) -lt $DURATION ]; do
    # Try to pay for the reservation
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
        -X POST "$GATEWAY/reserve/$RES_ID/pay" 2>/dev/null)
    
    if [ "$HTTP_CODE" = "200" ]; then
        ((SUCCESS++))
        echo -n "✓"
        # Create new reservation after successful payment
        RESP=$(curl -s -X POST "$GATEWAY/events/1/reserve" \
            -H "Content-Type: application/json" \
            -d '{"quantity": 1}')
        RES_ID=$(echo $RESP | grep -o '"reservation_id":"[^"]*"' | cut -d'"' -f4)
    else
        ((FAIL++))
        echo -n "✗"
    fi
    
    sleep 0.2
done

echo ""
echo "---"
echo "Results: Success=$SUCCESS Fail=$FAIL"
echo "Error rate: $((FAIL * 100 / (SUCCESS + FAIL)))%"
