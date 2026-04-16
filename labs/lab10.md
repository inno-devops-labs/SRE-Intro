# Lab 10 — SRE Portfolio & Reliability Review

![difficulty](https://img.shields.io/badge/difficulty-intermediate-yellow)
![topic](https://img.shields.io/badge/topic-SRE%20Portfolio-blue)
![points](https://img.shields.io/badge/points-10%2B2.5-orange)
![tech](https://img.shields.io/badge/tech-Locust%20%2B%20DORA-informational)

> **Goal:** Run load tests, calculate DORA metrics, identify toil, and write a reliability review — your capstone document.
> **Deliverable:** A PR from `feature/lab10` with `submissions/lab10.md` (reliability review). Submit PR link via Moodle.

---

## Overview

In this lab you will:
- Run load tests with Locust to find QuickTicket's breaking point
- Calculate DORA metrics from your Git/CI history
- Identify toil you encountered during the course
- Write a reliability review — the capstone SRE document

> **This is a synthesis lab.** No new tools to learn. You use everything from the last 9 weeks.

---

## Project State

**You should have from all previous labs:**
- QuickTicket with monitoring, alerting, CI/CD, GitOps, canary deployments, migrations, backups
- A full set of submissions documenting your work

**This lab produces:**
- A reliability review that demonstrates SRE thinking
- Your portfolio-ready repository

---

## Task 1 — Load Testing & Reliability Review (6 pts)

**Objective:** Find the system's limits and write a comprehensive reliability review.

### 10.1: Install and run Locust

```bash
pip install locust
```

Create `locustfile.py` in the repo root:

```python
from locust import HttpUser, task, between

class QuickTicketUser(HttpUser):
    wait_time = between(0.5, 2)

    @task(7)
    def list_events(self):
        self.client.get("/events")

    @task(2)
    def reserve(self):
        self.client.post("/events/1/reserve",
            json={"quantity": 1},
            headers={"Content-Type": "application/json"})

    @task(1)
    def health(self):
        self.client.get("/health")
```

Start QuickTicket with monitoring, then run Locust:

```bash
cd app/
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d

# Run Locust (headless mode for CLI output)
cd ..
locust -f locustfile.py --host=http://localhost:3080 --headless \
  -u 10 -r 2 -t 60s --csv=loadtest
```

This simulates 10 users with a ramp-up of 2 users/sec for 60 seconds.

Then increase load to find the breaking point:

```bash
locust -f locustfile.py --host=http://localhost:3080 --headless \
  -u 50 -r 5 -t 60s --csv=loadtest_50

locust -f locustfile.py --host=http://localhost:3080 --headless \
  -u 100 -r 10 -t 60s --csv=loadtest_100
```

At which user count does:
- p99 latency exceed 500ms (latency SLO)?
- Error rate exceed 0.5% (availability SLO)?

### 10.2: Calculate DORA metrics

From your Git and CI history, calculate:

```bash
# Deployment Frequency: how many deployments (commits to main) in the course?
git log --oneline main | wc -l

# Lead Time for Changes: average time from commit to deploy
# (approximate: time between commit and ArgoCD sync)

# Change Failure Rate: how many deployments caused issues?
# (count: bad deploys from Lab 5 rollback, Lab 7 abort, Lab 8 chaos)

# Recovery Time: how long from failure to recovery?
# (from your Lab 6 postmortem timeline and Lab 5 rollback timing)
```

### 10.3: Identify toil

List 3 repetitive manual tasks you did during the course. For each:
- What was the task?
- How often did you do it?
- How would you automate it?

### 10.4: Write the reliability review

Create `submissions/lab10.md` with:

```markdown
# QuickTicket Reliability Review

## 1. SLO Compliance
- Availability SLO: 99.5% — [met/not met] based on [evidence]
- Latency SLO: 95% < 500ms — [met/not met] based on [evidence]
- Error budget status: [how much consumed]

## 2. Load Test Results
- Breaking point: [X users / Y RPS]
- At 10 users: p99 = [X]ms, error rate = [Y]%
- At 50 users: p99 = [X]ms, error rate = [Y]%
- At 100 users: p99 = [X]ms, error rate = [Y]%

## 3. DORA Metrics
- Deployment Frequency: [X deploys over the course]
- Lead Time: [approximate]
- Change Failure Rate: [X out of Y deploys caused issues]
- Recovery Time: [from postmortem data]

## 4. Top 3 Reliability Risks
1. [Risk — why it matters — what would fix it]
2. [Risk — why it matters — what would fix it]
3. [Risk — why it matters — what would fix it]

## 5. Toil Identification
| Toil | Frequency | Automation Proposal |
|------|-----------|-------------------|
| [task] | [how often] | [how to automate] |

## 6. Monitoring Gaps
- What are we NOT monitoring that we should?

## 7. Capacity Plan
- Current breaking point: [X RPS]
- To handle 2x load: [what changes needed]
```

<details>
<summary>💡 Hints</summary>

- Locust `--csv` flag generates CSV files with statistics — useful for comparison
- For DORA, approximate is fine — you don't need precise timestamps for every deploy
- Monitoring gaps: think about what surprised you in chaos experiments (Lab 8) — was there something you wish you had been monitoring?
- Capacity: think about replicas, resource limits, connection pools, caching

</details>

---

## Task 2 — DORA Dashboard & Capacity Plan (4 pts)

> ⏭️ This task is optional. Skipping it will not affect future labs.

**Objective:** Build a DORA metrics visualization and a concrete capacity plan.

### 10.5: DORA in Grafana

Create a simple text panel or table in Grafana showing your calculated DORA metrics. Or use a stat panel with manual values.

### 10.6: Capacity plan

Based on load test results, answer:
- How many replicas of each service for 2x current traffic?
- What resource limits would you set?
- Would you add Redis caching? Connection pooling changes?
- Rough cost estimate: if each pod costs ~$5/month on a cloud provider, what's the monthly cost for 2x capacity?

**Add to `submissions/lab10.md`:**
- DORA dashboard evidence (or manual calculation with source data)
- Detailed capacity plan with numbers

---

## Bonus Task — Demo Video or SRE Handbook (2.5 pts)

> 🌟 For those who want extra challenge and experience.

**Option A: 5-minute demo video**

Record a screen recording walking through your QuickTicket SRE setup:
1. Show the architecture (docker-compose or k8s)
2. Show the monitoring dashboard (golden signals)
3. Inject a failure → show alert firing → show runbook → resolve
4. Show canary deployment (promote or abort)
5. Explain one SRE principle you learned

**Option B: SRE Handbook**

Write a 2-page "QuickTicket SRE Handbook" that a new team member could use:
- Architecture overview
- How to deploy (GitOps flow)
- Monitoring: what dashboards to check, what alerts exist
- Incident response: runbooks, escalation
- Backup/restore procedure

**Add to `submissions/lab10.md`** (for handbook) or link to video.

---

## How to Submit

```bash
git switch -c feature/lab10
git add locustfile.py submissions/lab10.md
git commit -m "feat(lab10): add load tests and reliability review"
git push -u origin feature/lab10
```

PR checklist:
```text
- [x] Task 1 done — load tests, DORA metrics, toil, reliability review
- [ ] Task 2 done — DORA dashboard + capacity plan
- [ ] Bonus Task done — demo video or SRE handbook
```

---

## Acceptance Criteria

### Task 1 (6 pts)
- ✅ Locust load tests at 3+ load levels with results
- ✅ DORA metrics calculated from project history
- ✅ 3 toil items identified with automation proposals
- ✅ Reliability review covering all 7 sections

### Task 2 (4 pts)
- ✅ DORA visualization or detailed calculation
- ✅ Capacity plan with concrete numbers

### Bonus Task (2.5 pts)
- ✅ 5-min demo video OR 2-page SRE handbook

---

## Rubric

| Task | Points | Criteria |
|------|-------:|----------|
| **Task 1** — Load tests + reliability review | **6** | Load tests, DORA, toil, comprehensive review |
| **Task 2** — DORA dashboard + capacity plan | **4** | Visualization, concrete capacity numbers |
| **Bonus Task** — Demo video or handbook | **2.5** | Comprehensive, clear, useful |
| **Total** | **12.5** | 10 main + 2.5 bonus |

---

## Resources

<details>
<summary>📚 Documentation</summary>

- [Locust Documentation](https://docs.locust.io/)
- [DORA Metrics](https://dora.dev/research/)
- [Google SRE Book — full index](https://sre.google/sre-book/table-of-contents/)

</details>
