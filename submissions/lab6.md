# Lab 6 report
# Runbook: QuickTicket High Error Rate

## Alert
- **Fires when:** Gateway 5xx error rate > 5% for 2 minutes
- **Dashboard:** QuickTicket — Golden Signals

## Diagnosis
1. Check which service is failing:
   - `curl -s http://localhost:3080/health | python3 -m json.tool`
2. Check payments service directly:
   - `curl -s http://localhost:8082/health`
3. Check events service:
   - `curl -s http://localhost:8081/health`
4. Check logs for errors:
   - `docker compose logs gateway --tail=20 --since=5m`
   - `docker compose logs payments --tail=20 --since=5m`

## Common Causes
| Cause | How to identify | Fix |
|-------|----------------|-----|
| Payments service down | health shows payments: down | Restart: `docker compose start payments` |
| Payments high failure rate | health OK but errors in logs | Check PAYMENT_FAILURE_RATE env var |
| Events service down | health shows events: down | Restart: `docker compose start events` |
| Database connection exhausted | events logs show pool errors | Restart events, check DB_MAX_CONNS |

## Escalation
- If not resolved in 10 minutes, escalate to: [instructor/TA]


## Task 1
**Paste into `submissions/lab6.md`:**
1. Your alert rule PromQL queries (both rules)
```
sum(rate(gateway_requests_total{status=~"5.."}[5m])) / sum(rate(gateway_requests_total[5m])) * 100

(1 - (sum(rate(gateway_requests_total{status!~"5.."}[30m])) / sum(rate(gateway_requests_total[30m])))) / (1 - 0.995)
```
2. Contact point type and evidence of notification received (webhook URL output or screenshot)
https://docs.google.com/document/d/19wQnwpX-Rr6mUXF7smK7T6yVRUrCsRm88TiHNwFaFUk/edit?usp=sharing
3. Your runbook (full text)
on the top
4. Alert firing evidence: Grafana alert rule status showing "Firing"
as i said in the link above
5. Timeline: when you injected → when alert fired → when you diagnosed → when you fixed → when alert resolved
Payments stopped at ~22:58. Error rate briefly spiked above 5% then settled on a plateau around 1-2% due to the 5 minute rolling window averaging it with normal traffic. It stayed there and never crossed 5% again, so the alert stayed in Normal state so no fire/diagnose/fix timeline for this run

6. Answer: "How long from failure injection to alert firing? Why the delay?"
The alert never fired. The gateway error rate plateaued at 1-2%, below the 5% threshold, because payments only make up a small fraction of total gateway traffic, even with payments fully down, overall error rate doesn't reach the threshold. This matches a known tuning issue called out in the lab: the 5% threshold assumes payments are a bigger share of traffic than they actually are, so the alert is under-sensitive for this failure mode as configured
## Task 2
# Postmortem: Payments Service Outage — Alert Failed to Fire

**Date:** July 3, 2026
**Duration:**  ~22:58 to 23:10 (payments down, never restored properly detected by alerting)
**Severity:** SEV-3
**Author:** Vladimir

## Summary
The payments service was intentionally stopped to simulate an outage. Gateway error rate briefly spiked above the 5% alert threshold but quickly settled at 1-2% and stayed there, so the alert never fired and no notification was sent

## Timeline
| Time | Event |
|------|-------|
| 22:58 | Payments service stopped (failure injected)|
| 22:58 | Error rate briefly spiked above 5% threshold |
| 22:59 | Error rate dropped back to 1-2% and plateaued |
| 23:00-23:07 | Error rate stayed on plateau, alert remained in Normal state |
| 23:07 | Confirmed via Grafana that alert never reached Firing |

## Root Cause
The alert query averages error rate over a 5 minute rolling window. Payments requests are only a small fraction of total gateway traffic, so even with payments fully down, the overall error rate never sustained above the 5% threshold long enough to satisfy the 2 minute pending period. The threshold was calibrated assuming a higher share of payment traffic than actually exists

## What Went Well
- Failure injection worked as expected (payments went fully down)
- Alert rule, contact point, and notification policy were all configured correctly
- The dashboard clearly showed the real error rate behavior, making the root cause easy to identify
## What Went Wrong
- The 5% threshold was too high relative to the actual traffic mix, so the alert never fired
- No secondary alert existed scoped specifically to payments errors, only the overall gateway error rate

## Action Items
| Action | Owner | Priority |
|--------|-------|----------|
| Add a payments-specific error rate alert, scoped to service="payments" traffic only | Vladimir | High |
| Re-tune the gateway-wide error rate threshold based on real traffic proportions | Vladimir | Medium |
|Document expected traffic mix (% payments vs other endpoints) in the runbook | Vladimir | Low|

Most important action item: Adding a payments-specific alert, because the current gateway-wide alert is structurally blind to payments-only outages when payments make up a small share of total traffic — this is the actual gap that caused the failed detection, not just a threshold tuning issue.