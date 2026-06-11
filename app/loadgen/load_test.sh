#!/usr/bin/env bash
GATEWAY="http://localhost:3080"
DURATION=30
SUCCESS=0
FAIL=0
START=$(date +%s)

echo "Load test: 5 requests/sec for ${DURATION}s"
echo "Press Ctrl+C to stop early"
echo "---"

while [ $(($(date +%s) - START)) -lt $DURATION ]; do
    # Reserve ticket
    STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
        -X POST "$GATEWAY/events/1/reserve" \
        -H "Content-Type: application/json" \
        -d '{"quantity": 1}' 2>/dev/null)
    
    if [ "$STATUS" = "200" ]; then
        ((SUCCESS++))
        echo -n "✓"
    else
        ((FAIL++))
        echo -n "✗"
    fi
    
    sleep 0.2
done

echo ""
echo "---"
echo "Results: Success=$SUCCESS Fail=$FAIL"
echo "Success rate: $((SUCCESS * 100 / (SUCCESS + FAIL)))%"
