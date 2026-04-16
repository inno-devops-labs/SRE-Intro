# Lab 8 — Chaos Engineering: Break Things on Purpose

![difficulty](https://img.shields.io/badge/difficulty-intermediate-yellow)
![topic](https://img.shields.io/badge/topic-Chaos%20Engineering-blue)
![points](https://img.shields.io/badge/points-10%2B2.5-orange)
![tech](https://img.shields.io/badge/tech-kubectl%20%2B%20env%20vars-informational)

> **Goal:** Design and execute chaos experiments with hypotheses, observe system behavior, and document findings.
> **Deliverable:** A PR from `feature/lab8` with `submissions/lab8.md` containing 3 experiment reports. Submit PR link via Moodle.

---

## Overview

In this lab you will practice:
- Writing chaos experiment hypotheses using the scientific method
- Injecting failures using kubectl and environment variables
- Observing system behavior through monitoring dashboards
- Comparing expected vs actual results
- Identifying resilience improvements

> **No extra tools needed.** You use `kubectl`, environment variables, and your existing monitoring stack. QuickTicket has built-in fault injection — use it.

---

## Project State

**You should have from previous labs:**
- QuickTicket on k3d with monitoring (Prometheus + Grafana)
- Grafana alerting configured (from Lab 6)
- Understanding of the 3-service architecture and failure modes (Lab 1)

**This lab adds:**
- Structured chaos experiments with hypothesis-driven approach
- Documentation of system resilience weaknesses

---

## Task 1 — Three Chaos Experiments (6 pts)

**Objective:** Design and execute 3 chaos experiments. For each: write hypothesis, inject failure, observe, compare with hypothesis, document findings.

Start the full stack with monitoring and loadgen:

```bash
cd app/
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d --build
./loadgen/run.sh 3 600 &
```

Wait 1-2 minutes for metrics baseline to establish. Open Grafana golden signals dashboard.

### Experiment 1 — Pod Kill Under Load

**Write your hypothesis first** (before running the experiment):

```
HYPOTHESIS: "If I delete the gateway pod while traffic is flowing,
[expected behavior] will happen because [reason]."
```

**Execute:**
```bash
kubectl delete pod -l app=gateway
```

**Observe (document all of these):**
- How long until K8s creates a new pod? (`kubectl get pods -w`)
- How many requests failed during the gap? (check loadgen output or Grafana error rate)
- Did the alert fire?
- How long was the total user impact?

**Compare:** Was your hypothesis correct? What surprised you?

### Experiment 2 — Payment Latency Injection

**Hypothesis:**
```
HYPOTHESIS: "If payments takes 2 seconds per request,
[expected behavior] will happen because [reason]."
```

**Execute:**
```bash
# Restart payments with 2-second latency
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml stop payments
PAYMENT_LATENCY_MS=2000 docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d payments
```

**Observe:**
- Does the gateway timeout? (GATEWAY_TIMEOUT_MS is 5000ms, payments takes 2000ms — will it fit?)
- What happens to p99 latency on the dashboard?
- Do error rates change?
- How does this differ from payments being completely down?

**Restore:** `docker compose stop payments && PAYMENT_LATENCY_MS=0 docker compose up -d payments`

### Experiment 3 — Redis Failure

**Hypothesis:**
```
HYPOTHESIS: "If Redis goes down, [expected behavior]
will happen because [reason]."
```

**Execute:**
```bash
kubectl scale deployment redis --replicas=0
# or if using docker-compose:
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml stop redis
```

**Observe:**
- Can users still list events? (list doesn't need Redis)
- Can users reserve tickets? (reserve NEEDS Redis)
- What does the health endpoint report?
- How long until you notice on the dashboard?

**Restore:** `docker compose start redis` or `kubectl scale deployment redis --replicas=1`

### Proof of work

**Paste into `submissions/lab8.md`:**

For EACH experiment (3 total):
1. Your hypothesis (written BEFORE running)
2. The command(s) you ran
3. What you observed (CLI output, dashboard changes, alert status)
4. Comparison: hypothesis vs reality — what matched, what surprised you
5. One sentence: "To improve resilience against this failure, I would..."

<details>
<summary>💡 Hints</summary>

- Write the hypothesis FIRST — the learning comes from the surprise when reality differs
- Keep loadgen running during all experiments so you have traffic to observe
- Take note of timestamps — match them to Grafana dashboard
- "No impact" is a valid and interesting observation (it means K8s self-healing worked!)
- Experiment 2 (latency) is often more interesting than outage — partial degradation is harder to detect
- After each experiment, wait for full recovery before starting the next one

</details>

---

## Task 2 — Combined Failure Scenario (4 pts)

> ⏭️ This task is optional. Skipping it will not affect future labs.

**Objective:** Design and execute a more complex scenario with multiple simultaneous failures.

### 8.4: Design a combined scenario

Real incidents often involve multiple failures at once. Design a scenario using 2+ fault injection methods:

**Example scenarios (pick one or design your own):**
- **"Degraded dependencies":** payments 30% failure rate + 500ms latency + DB connections limited to 3
- **"Cascade test":** kill Redis + inject payment latency — does gateway handle the combination gracefully?
- **"Capacity crunch":** increase loadgen to 10 RPS + limit DB connections to 2 — where does the system break first?

### 8.5: Execute and document

```bash
# Example: degraded dependencies
docker compose stop payments
PAYMENT_FAILURE_RATE=0.3 PAYMENT_LATENCY_MS=500 docker compose up -d payments
kubectl set env deployment/events DB_MAX_CONNS=3  # if on K8s
```

**Observe for 3-5 minutes.** Document:
- Which golden signal reacted first?
- Did alerts fire? Which ones?
- What was the user experience? (run manual curl requests alongside)

**Paste into `submissions/lab8.md`:**
- Your combined scenario design (what + why)
- All observations with timestamps
- Answer: "Which component was the weakest link? How would you make it more resilient?"

---

## Bonus Task — Resilience Improvement (2.5 pts)

> 🌟 For those who want extra challenge and experience.

**Objective:** Fix one weakness found in your experiments, then re-run the experiment to prove it's better.

### B.1: Choose a weakness

From your experiments, pick the failure that had the worst impact. Examples:
- Gateway doesn't handle payments timeout gracefully
- No fallback when Redis is down
- Alerts didn't fire for partial degradation

### B.2: Implement a fix

Make a code change to improve resilience. Examples:
- Add a timeout to gateway's payment call
- Make events return cached data when Redis is down
- Lower the alert threshold based on what you learned in Lab 6

### B.3: Re-run the experiment

Run the same experiment as before. Compare:
- Before fix: [impact]
- After fix: [impact]

**Paste into `submissions/lab8.md`:**
- Which weakness you chose
- What you changed (code diff or config change)
- Before vs after comparison
- Evidence that the fix worked (CLI output or dashboard)

---

## How to Submit

```bash
git switch -c feature/lab8
git add submissions/lab8.md
git commit -m "feat(lab8): add chaos experiment reports"
git push -u origin feature/lab8
```

PR checklist:
```text
- [x] Task 1 done — 3 chaos experiments with hypotheses
- [ ] Task 2 done — combined failure scenario
- [ ] Bonus Task done — resilience improvement with before/after proof
```

---

## Acceptance Criteria

### Task 1 (6 pts)
- ✅ 3 experiments, each with: hypothesis, method, observations, comparison
- ✅ Hypotheses written BEFORE executing (not retrofitted)
- ✅ CLI output or dashboard evidence for each experiment
- ✅ At least one "I would improve..." statement per experiment

### Task 2 (4 pts)
- ✅ Combined scenario with 2+ simultaneous failures
- ✅ Observations with timestamps
- ✅ Weakest link identified with explanation

### Bonus Task (2.5 pts)
- ✅ Weakness chosen from experiments
- ✅ Fix implemented (code diff or config change)
- ✅ Before vs after comparison with evidence

---

## Rubric

| Task | Points | Criteria |
|------|-------:|----------|
| **Task 1** — 3 chaos experiments | **6** | Hypotheses + execution + observations + comparison for each |
| **Task 2** — Combined failure scenario | **4** | Multi-failure design, observations, weakest link analysis |
| **Bonus Task** — Resilience improvement | **2.5** | Fix implemented, before/after evidence |
| **Total** | **12.5** | 10 main + 2.5 bonus |

---

## Resources

<details>
<summary>📚 Documentation</summary>

- [Principles of Chaos Engineering](https://principlesofchaos.org/)
- [Netflix Chaos Monkey](https://netflix.github.io/chaosmonkey/)
- [Google SRE Book, Ch 17 — Testing for Reliability](https://sre.google/sre-book/testing-reliability/)

</details>

<details>
<summary>⚠️ Common Pitfalls</summary>

- **Forgot to start loadgen** — experiments without traffic show nothing. Always have load running.
- **Didn't write hypothesis first** — the learning is in the surprise. Retrofitting defeats the purpose.
- **Restored too fast** — wait 2-3 minutes to observe full impact before restoring.
- **Combined experiment without baseline** — run each failure individually first, then combine.

</details>
