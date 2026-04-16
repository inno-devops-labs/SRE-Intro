# Lab 6 — Alerting & Incident Response

![difficulty](https://img.shields.io/badge/difficulty-intermediate-yellow)
![topic](https://img.shields.io/badge/topic-Alerting%20%26%20Incidents-blue)
![points](https://img.shields.io/badge/points-10%2B2.5-orange)
![tech](https://img.shields.io/badge/tech-Grafana%20Alerting-informational)

> **Goal:** Create SLO-based alerts in Grafana, simulate an incident, follow your own runbook, and write a blameless postmortem.
> **Deliverable:** A PR from `feature/lab6` with `submissions/lab6.md` containing alert configs, runbook, and postmortem. Submit PR link via Moodle.

---

## Overview

In this lab you will practice:
- Creating alert rules in Grafana based on SLO burn rates
- Configuring notification channels (contact points)
- Writing a runbook for your alert
- Injecting a failure and responding using your runbook
- Writing a blameless postmortem

> **You write everything.** Alert rules, runbook, postmortem — all created by you. The only thing provided is the monitoring stack from Lab 3.

---

## Project State

**You should have from previous labs:**
- QuickTicket on k3d with monitoring (Prometheus + Grafana)
- SLOs defined (99.5% availability from Lab 3)

**This lab adds:**
- Grafana alert rules that fire when SLOs are threatened
- A runbook for responding to alerts
- Your first blameless postmortem

---

## Task 1 — Create Alerts & Respond to an Incident (6 pts)

**Objective:** Set up SLO-based alerting, inject a failure, detect it via alert, and resolve it.

### 6.1: Start the full stack

```bash
cd app/
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d --build
```

Generate background traffic so metrics are populated:

```bash
./loadgen/run.sh 3 300 &
```

### 6.2: Create a contact point

In Grafana (`http://localhost:3000`), go to **Alerting → Contact points → Add contact point**:

- **Name:** `quickticket-alerts`
- **Type:** Webhook (simplest — sends JSON POST to a URL)
- **URL:** Use a free webhook receiver like https://webhook.site (gives you a unique URL to see notifications)

Alternatively, if you have Discord/Slack: use the corresponding integration.

Test the contact point — click "Test" and verify you receive a notification.

### 6.3: Create alert rules

Go to **Alerting → Alert rules → New alert rule**.

**Alert 1 — High Error Rate (critical):**
- **Name:** `QuickTicket High Error Rate`
- **Type:** Grafana-managed
- **Query A (PromQL):**
  ```promql
  sum(rate(gateway_requests_total{status=~"5.."}[5m])) / sum(rate(gateway_requests_total[5m])) * 100
  ```
- **Condition:** IS ABOVE `5` (5% error rate)
- **Evaluation:** every 1m, for 2m (pending period — must be true for 2 min before firing)
- **Labels:** `severity=critical`
- **Annotations:**
  - Summary: `Gateway error rate is {{ $value }}%`
  - Description: `Error rate exceeded 5% for 2 minutes. Check payments service health.`

**Alert 2 — SLO Burn Rate (warning):**
- **Name:** `QuickTicket SLO Burn Rate`
- **Query A:**
  ```promql
  (1 - (sum(rate(gateway_requests_total{status!~"5.."}[30m])) / sum(rate(gateway_requests_total[30m])))) / (1 - 0.995)
  ```
- **Condition:** IS ABOVE `6` (6x burn rate — will exhaust 30-day budget in 5 days)
- **Evaluation:** every 1m, for 5m
- **Labels:** `severity=warning`

### 6.4: Configure notification policy

Go to **Alerting → Notification policies**. Edit the default policy:
- **Default contact point:** `quickticket-alerts`
- **Group by:** `alertname`
- **Group wait:** 30s (wait before first notification)
- **Repeat interval:** 5m

### 6.5: Write a runbook

Create a runbook for the "High Error Rate" alert. Use this structure:

```markdown
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
```

Save this in `submissions/lab6.md` as part of your submission.

### 6.6: Inject failure and respond

Now simulate a real incident. Inject payment failures:

```bash
# Stop payments and restart with 50% failure rate
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml stop payments
PAYMENT_FAILURE_RATE=0.5 docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d payments
```

**Now respond as if this is a real incident:**

1. Wait for the alert to fire (check Grafana Alerting → Alert rules — status should change to "Firing")
2. Check your webhook/Discord/Slack — did the notification arrive?
3. Follow your runbook step by step — diagnose, identify the cause, fix it
4. Fix: restore normal payments:
   ```bash
   docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml stop payments
   PAYMENT_FAILURE_RATE=0.0 docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d payments
   ```
5. Wait for the alert to resolve (status → "Normal")

**Record timestamps** for everything — you need them for the postmortem.

### 6.7: Proof of work

**Paste into `submissions/lab6.md`:**
1. Your alert rule PromQL queries (both rules)
2. Contact point type and evidence of notification received (webhook URL output or screenshot)
3. Your runbook (full text)
4. Alert firing evidence: Grafana alert rule status showing "Firing"
5. Timeline: when you injected → when alert fired → when you diagnosed → when you fixed → when alert resolved
6. Answer: "How long from failure injection to alert firing? Why the delay?"

<details>
<summary>💡 Hints</summary>

- Alert "pending period" means it must be true for N minutes before firing — a 2-min pending period + 1-min evaluation = ~3 min from failure to alert
- Webhook.site gives you a free URL: visit the site, copy your unique URL, paste as contact point
- If "No data" in alert query — ensure loadgen is running and metrics are flowing
- The SLO burn rate query uses 30m window — it needs 30 min of data to evaluate properly. Start with the error rate alert first (faster to test)
- `PAYMENT_FAILURE_RATE=0.5` means 50% of charge requests fail — but charges are only ~10% of traffic, so overall error rate is ~1-2%, not 50%! You may need to **lower your threshold** or kill payments entirely to trigger the alert. This threshold tuning is a real SRE skill.

</details>

---

## Task 2 — Blameless Postmortem (3 pts)

> ⏭️ This task is optional. Skipping it will not affect future labs.

**Objective:** Write a blameless postmortem for the incident you just simulated.

### 6.8: Write the postmortem

Use this structure:

```markdown
# Postmortem: [Incident Title]

**Date:** [date]
**Duration:** [start → end]
**Severity:** SEV-[1-4]
**Author:** [your name]

## Summary
[1-2 sentences: what happened and impact]

## Timeline
| Time | Event |
|------|-------|
| HH:MM | [failure injected / first symptom] |
| HH:MM | [alert fired] |
| HH:MM | [investigation started] |
| HH:MM | [root cause identified] |
| HH:MM | [fix applied] |
| HH:MM | [alert resolved / service recovered] |

## Root Cause
[Systemic cause — NOT "I changed the env var." Instead: "The payments service
failure rate was increased to 50%, causing gateway to return 502 errors for
all payment requests. This burned 5% of the weekly error budget in 10 minutes."]

## What Went Well
- [e.g., Alert fired within 3 minutes of failure]
- [e.g., Runbook was clear and easy to follow]

## What Went Wrong
- [e.g., Took 5 minutes to find the right dashboard]
- [e.g., Runbook didn't cover this specific failure mode]

## Action Items
| Action | Owner | Priority |
|--------|-------|----------|
| [e.g., Add alert for payment latency spike] | [name] | High |
| [e.g., Update runbook with env var check] | [name] | Medium |
```

**Important:** This is a **blameless** postmortem. Focus on **systems and processes**, not "I made a mistake." The question isn't "who broke it" but "how did the system allow this to happen?"

**Paste into `submissions/lab6.md`:**
- Full postmortem document
- Answer: "What is the most important action item from your postmortem? Why?"

---

## Bonus Task — Cross-Test Runbooks (2.5 pts)

> 🌟 For those who want extra challenge and experience.

**Objective:** Write a runbook for a *different* failure mode, and have a classmate test it.

### B.1: Write a second runbook

Choose a failure mode you haven't covered yet:
- Redis down → reservations fail
- PostgreSQL down → everything fails
- Gateway timeout too short → intermittent failures under load
- DB connection pool exhausted → events service degraded

Write a runbook following the same structure as step 6.5.

### B.2: Swap and test

1. Give your runbook to a classmate (they should NOT know what failure you'll inject)
2. Inject the failure in your stack
3. Your classmate follows ONLY the runbook to diagnose and fix it
4. Record: Did they succeed? How long did it take? What was unclear?

**Paste into `submissions/lab6.md`:**
- Your second runbook
- Results: Did the classmate resolve it using only the runbook?
- What they found unclear or missing → update the runbook based on feedback

---

## How to Submit

```bash
git switch -c feature/lab6
git add submissions/lab6.md
git commit -m "feat(lab6): add alerting config, runbook, and postmortem"
git push -u origin feature/lab6
```

PR checklist:
```text
- [x] Task 1 done — alerts created, incident simulated, runbook followed
- [ ] Task 2 done — blameless postmortem written
- [ ] Bonus Task done — cross-tested runbook with classmate
```

---

## Acceptance Criteria

### Task 1 (6 pts)
- ✅ Two alert rules created in Grafana (error rate + burn rate)
- ✅ Contact point configured and tested
- ✅ Runbook written with diagnosis + mitigation + escalation
- ✅ Alert fired during failure injection (evidence provided)
- ✅ Timeline recorded from injection to resolution
- ✅ Written answer about alert delay

### Task 2 (3 pts)
- ✅ Full blameless postmortem following the template
- ✅ Focus on systems, not blame
- ✅ Action items are specific and assigned

### Bonus Task (2.5 pts)
- ✅ Second runbook for a different failure mode
- ✅ Classmate tested it
- ✅ Runbook updated based on feedback

---

## Rubric

| Task | Points | Criteria |
|------|-------:|----------|
| **Task 1** — Alerts + runbook + incident | **6** | Both alerts configured, runbook written, failure detected + resolved, timestamps documented |
| **Task 2** — Blameless postmortem | **3** | Full postmortem, blameless tone, concrete action items |
| **Bonus Task** — Cross-tested runbook | **2.5** | Second runbook, peer-tested, updated from feedback |
| **Total** | **11.5** | 9 main + 2.5 bonus |

---

## Resources

<details>
<summary>📚 Documentation</summary>

- [Grafana Alerting docs](https://grafana.com/docs/grafana/latest/alerting/)
- [Google SRE Workbook, Ch 5 — Alerting on SLOs](https://sre.google/workbook/alerting-on-slos/)
- [PagerDuty Incident Response (open source)](https://response.pagerduty.com/)
- [Google SRE Book, Ch 15 — Postmortem Culture](https://sre.google/sre-book/postmortem-culture/)

</details>

<details>
<summary>⚠️ Common Pitfalls</summary>

- **Alert never fires** — check: is loadgen running? Is the pending period too long? Is the PromQL query returning data?
- **Alert fires immediately** — pending period too short, or threshold too low
- **Notification not received** — test the contact point first (Grafana has a "Test" button)
- **Burn rate query shows NaN** — not enough data in the window yet. Use the error rate alert for faster testing.
- **Two compose files** — always run from `app/`: `docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml`

</details>
