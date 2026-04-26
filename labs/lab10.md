# Lab 10 — SRE Portfolio & Reliability Review

![difficulty](https://img.shields.io/badge/difficulty-intermediate-yellow)
![topic](https://img.shields.io/badge/topic-SRE%20Portfolio-blue)
![points](https://img.shields.io/badge/points-10%2B2-orange)
![tech](https://img.shields.io/badge/tech-Locust%20%2B%20DORA-informational)

> **Goal:** Run Locust load tests to find QuickTicket's breaking point, calculate DORA metrics, identify toil, and write a reliability review — your capstone document.
> **Deliverable:** A PR from `feature/lab10` with `locustfile.py` at the repo root and `submissions/lab10.md` (reliability review). Submit PR link via Moodle.

---

## Overview

In this lab you will:

- Run Locust load tests **in-cluster** at several load levels to find QuickTicket's breaking point
- Calculate DORA metrics from your Git + Argo Rollouts history
- Identify the toil you encountered across Labs 1-9
- Write the reliability review — the capstone SRE document that ties everything together

> **This is a synthesis lab.** No new infrastructure to stand up. You use the k3d cluster, monitoring, and CI/CD from previous weeks.

---

## Project State

**You should have from previous labs:**

- QuickTicket on k3d with 5 gateway replicas (from Lab 7) and Postgres on a PVC (from Lab 9 Bonus).
- In-cluster Prometheus in the `monitoring` namespace (Lab 7 Bonus).
- A full set of submissions and git history covering Labs 1-9.

**This lab produces:**

- `locustfile.py` (at repo root) — the Locust scenario you reuse for future capacity tests.
- `submissions/lab10.md` — your reliability review.

---

## Setup

> ⚠️ **Do NOT load-test through `kubectl port-forward svc/gateway`.** That command picks one endpoint and stays there — you'll only exercise 1 of your 5 gateway pods and wrongly conclude the system can only handle a fraction of its real capacity. The load generator has to live **inside** the cluster so traffic goes through kube-proxy and is distributed across all replicas.

The provided files are:

- [`labs/lab10/locustfile.py`](./lab10/locustfile.py) — the Locust scenario (read/reserve/health task mix, split across events 3 and 5).
- [`labs/lab10/locust-runner.yaml`](./lab10/locust-runner.yaml) — the Kubernetes Job template that runs Locust against `http://gateway:8080` from inside the cluster.

Copy the scenario to your repo root (so it's committed as part of your portfolio) and load it into a ConfigMap the Job will mount:

```bash
cp labs/lab10/locustfile.py locustfile.py

kubectl create configmap locustfile \
  --from-file=locustfile.py=locustfile.py \
  --dry-run=client -o yaml | kubectl apply -f -

# Re-run the `kubectl create configmap …` line any time you edit locustfile.py
```

Before you start load testing, **flush Redis** so stale reservation-holds from Labs 7-9 don't pollute inventory:

```bash
kubectl exec -i $(kubectl get pod -l app=redis -o name) -- redis-cli FLUSHDB
```

---

## Task 1 — Load Testing & Reliability Review (6 pts)

**Objective:** Find the system's limits and write a comprehensive reliability review.

### 10.1: Run Locust at three load levels

For each level, create a Job that runs Locust in the cluster (hits `http://gateway:8080` — kube-proxy load-balances). Example for 10 users; repeat for 50 and 100:

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: load-10
spec:
  backoffLimit: 0
  ttlSecondsAfterFinished: 600
  template:
    spec:
      restartPolicy: Never
      containers:
        - name: locust
          image: locustio/locust:2.43.4
          command: ["locust"]
          args:
            - -f
            - /mnt/locust/locustfile.py
            - --host=http://gateway:8080
            - --headless
            - -u
            - "10"                        # ← users
            - -r
            - "2"                         # ← ramp-up /s
            - -t
            - "60s"
            - --only-summary
          volumeMounts:
            - { name: locustfile, mountPath: /mnt/locust }
      volumes:
        - name: locustfile
          configMap: { name: locustfile }
```

Between runs, **flush Redis** so the previous run's held seats don't count against the next:

```bash
kubectl exec -i $(kubectl get pod -l app=redis -o name) -- redis-cli FLUSHDB
```

After each Job finishes:

```bash
kubectl logs job/load-10 | tail -40
```

Fill in the table:

| Users | Ramp | RPS | p50 | p95 | p99 | 5xx error rate | 409 (inventory) |
|------:|-----:|----:|----:|----:|----:|---------------:|----------------:|
| 10    | 2/s  | ?   | ?   | ?   | ?   | ?              | ?               |
| 50    | 5/s  | ?   | ?   | ?   | ?   | ?              | ?               |
| 100   | 10/s | ?   | ?   | ?   | ?   | ?              | ?               |

> 💡 **Distinguish 409 from 5xx.** 409 Conflict on `/reserve` = inventory exhausted (expected product behavior when many clients race for limited tickets). 5xx = real system failure. Your SLO is about 5xx, not 409.

### 10.2: Find the breaking point

Keep increasing user count until **5xx error rate exceeds 0.5%** OR **p99 latency exceeds 500ms**. Note both the user count and the RPS at that point — that's your capacity ceiling.

Try 200u if 100u looked healthy:

```bash
# same Job YAML but -u 200 -r 20
```

### 10.3: Calculate DORA metrics

From your Git history and Argo Rollouts state:

```bash
# Deployment Frequency — count distinct rollouts / set-image operations
kubectl get rs -l app=gateway -o jsonpath='{.items[*].metadata.name}' | tr ' ' '\n' | wc -l
git log --oneline main | wc -l

# Lead Time — commit to ArgoCD sync
# approximate: your CI build time + 3-minute ArgoCD poll interval

# Change Failure Rate — count AnalysisRun failures + rolloutAborted
kubectl get analysisrun -o jsonpath='{.items[*].status.phase}' | tr ' ' '\n' | sort | uniq -c

# Recovery Time — argo rollouts abort → stable (~seconds)
#                 OR git revert → ArgoCD sync (~3 min)
```

### 10.4: Identify 3 pieces of toil

Look at your submissions from Labs 1-9. For each, list tasks you did manually **more than 3 times**. Good candidates:

- Running `kubectl exec ... psql -U quickticket -d quickticket < seed.sql` (every Postgres restart before you added the PVC).
- Re-creating port-forwards after pod restarts.
- Manually watching a canary rollout (`kubectl argo rollouts get rollout --watch`) instead of relying on the AnalysisTemplate.

For each: **how often**, **how to automate**, **what you'd save**.

### 10.5: Write the reliability review

Create `submissions/lab10.md`. Use this structure (fill in with YOUR data):

```markdown
# QuickTicket Reliability Review

## 1. SLO Compliance
| SLO | Target | Observed | Status |
| ... | ...    | ...      | ...    |

## 2. Load Test Results
[table from 10.1 + 10.2]

## 3. DORA Metrics
[table from 10.3]

## 4. Top 3 Reliability Risks
1. [Risk — why it matters — what would fix it]
2. ...
3. ...

## 5. Toil Identification
[table from 10.4]

## 6. Monitoring Gaps
- What you wished you had been monitoring during Lab 8 chaos experiments.
- What alert would have caught the thing that actually broke?

## 7. Capacity Plan
- Current ceiling: [X RPS]
- For 2x traffic, scale: [numbers]
- Rough cost estimate.
```

### 10.6: Proof of work

**Commit `locustfile.py` to your fork** (with the reserve-across-events-3-and-5 pattern so inventory doesn't dominate).

**Paste into `submissions/lab10.md`:**

1. Your load-test table across 10/50/100 (and the breaking-point level).
2. Your DORA metrics table.
3. Your top 3 risks + fixes.
4. Your toil table.
5. Your monitoring-gap list.
6. Your 2× capacity plan.

<details>
<summary>💡 Hints</summary>

- In the starter `locustfile.py`, the `reserve` task hits events 3 and 5. Event 3 has 500 tickets, event 5 has 80. Under 50u+ load you'll saturate event 5 within seconds — that generates the 409s. Distinguishing those from 5xx is an important habit.
- Locust `--only-summary` suppresses per-second output so you just get the final table. Remove it if you want to see rate evolution during the run.
- If you're stuck finding monitoring gaps, go back to your Lab 6 alert rules. Did you alert on **latency** or only on error rate? If only on error rate, a slow-but-successful dependency won't page anyone.
- DORA elite targets (2023 report): deploy on-demand, lead time <1 day, change failure rate 0-15%, recovery <1 hour. Don't be discouraged if you don't hit elite — you're a solo student, not a 20-person platform team.

</details>

---

## Task 2 — Capacity Plan with Numbers (4 pts)

> ⏭️ This task is optional. Skipping it will not affect your course grade.

**Objective:** Turn the reliability-review capacity plan into concrete numbers.

### 10.7: Measure per-pod headroom

At your breaking-point load level (where 5xx started appearing), sample per-pod CPU:

```bash
kubectl top pods -l app=gateway
kubectl top pods -l app=events
kubectl top pods -l app=payments
```

Which service is the CPU-constrained one? Which is idle? That tells you what to scale.

### 10.8: For 2× traffic, answer

- How many replicas of each service?
- What resource requests/limits?
- Redis — still single-pod OK, or do you need a replicated setup?
- DB connections — is the single-pooler-to-single-Postgres path a bottleneck?
- Rough cost estimate ($5/pod/mo is a reasonable small-cloud assumption).

**Paste into `submissions/lab10.md`:**

- Per-pod CPU at breaking point.
- Detailed capacity plan with replica counts, resource limits, cost.

---

## Bonus Task — 5-minute Walkthrough (2 pts)

> 🌟 For those who want extra challenge and experience.

Produce ONE of the following:

**Option A: 5-minute demo video**

Screen-record yourself walking through your QuickTicket setup:

1. Show the cluster (`kubectl get pods,svc,rollouts`).
2. Open the golden-signals metrics in Prometheus UI.
3. Trigger a failure and show how your monitoring catches it (you can re-use a Lab 8 experiment).
4. Trigger a canary rollout and show AnalysisRun deciding.
5. Explain one SRE principle you actually felt during the course.

Upload to YouTube (unlisted), paste the link in `submissions/lab10.md`.

**Option B: 2-page SRE handbook**

Write `submissions/runbooks/quickticket-handbook.md`:

- **Architecture** — 1 diagram + bullets (<½ page).
- **How to deploy** — the exact GitOps flow a new team member would follow.
- **Monitoring** — which dashboards/queries to check for what.
- **Incident response** — a distilled runbook (from Lab 6) + escalation.
- **Backup/restore** — the Lab 9 procedure, condensed.

---

## How to Submit

```bash
git switch -c feature/lab10
git add locustfile.py submissions/lab10.md
# plus submissions/runbooks/ if you did Bonus Option B
git commit -m "feat(lab10): add load tests and reliability review"
git push -u origin feature/lab10
```

PR checklist:

```text
- [x] Task 1 done — load tests, DORA, toil, reliability review (all 7 sections)
- [ ] Task 2 done — detailed capacity plan with numbers
- [ ] Bonus Task done — demo video OR SRE handbook
```

---

## Acceptance Criteria

### Task 1 (6 pts)
- ✅ Load-test table covering at least 10u / 50u / 100u (+ breaking point).
- ✅ Locust running in-cluster (not through port-forward — there's a reason).
- ✅ DORA metrics calculated from actual project history, with source data.
- ✅ 3 toil items identified with concrete automation proposals.
- ✅ All 7 reliability-review sections present and filled in.

### Task 2 (4 pts)
- ✅ `kubectl top pods` output at breaking-point load.
- ✅ Replica + resource + cost plan for 2× capacity.

### Bonus Task (2 pts)
- ✅ Demo video link OR completed 2-page handbook.

---

## Rubric

| Task | Points | Criteria |
|------|-------:|----------|
| **Task 1** — Load tests + reliability review | **6** | All 7 review sections; real load-test data; DORA with source data |
| **Task 2** — Capacity plan | **4** | Real CPU numbers + concrete 2× plan + cost |
| **Bonus Task** — Demo video or handbook | **2** | Clear, useful, covers all listed items |
| **Total** | **12** | 10 main + 2 bonus |

---

## Resources

<details>
<summary>📚 Documentation</summary>

- [Locust Documentation](https://docs.locust.io/)
- [Locust Kubernetes distributed mode](https://docs.locust.io/en/stable/running-in-docker.html) (for later, when you need more than one load-gen pod)
- [DORA Metrics](https://dora.dev/research/) — the research behind the Accelerate book
- [Google SRE Book — full index](https://sre.google/sre-book/table-of-contents/)
- [Google SRE Workbook, Ch. 4 — Service Level Objectives](https://sre.google/workbook/implementing-slos/)

</details>

<details>
<summary>⚠️ Common Pitfalls</summary>

- **Port-forward ≠ load-balancing.** `kubectl port-forward svc/gateway` targets one pod. Load-test from in-cluster (the provided Job pattern) or you'll wrongly blame the system.
- **Stale Redis holds dominate your "failures".** Always `FLUSHDB` between runs — otherwise you're measuring inventory contention, not system capacity.
- **409 is not 5xx.** An SLO cares about the system failing (5xx), not about a ticket being sold out (409). Counting them together ruins your report.
- **Locust `--only-summary` hides progress.** Drop it when debugging a run; keep it for clean final outputs.
- **`kubectl top pods` returns nothing.** metrics-server needs ~60s after cluster start to populate; k3d ships it preinstalled.
- **`kubectl get rs` shows many replicasets.** Argo Rollouts keeps history — current stable is the one whose Deployment controller has replicas > 0.

</details>
