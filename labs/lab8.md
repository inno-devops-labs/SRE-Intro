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
- Injecting failures using `kubectl` and environment variables
- Observing system behavior through the in-cluster Prometheus from Lab 7
- Comparing expected vs actual results
- Identifying resilience improvements

> **No extra tools needed.** You use `kubectl`, Kubernetes env vars, and Prometheus queries. QuickTicket has built-in fault injection — use it.

---

## Project State

**You should have from previous labs:**
- QuickTicket running on **k3d** (from Lab 4 onward)
- `gateway` as an Argo Rollouts Rollout with 5 replicas (from Lab 7)
- In-cluster Prometheus in the `monitoring` namespace (from Lab 7 bonus — `labs/lab7/prometheus.yaml`)

**This lab adds:**
- A more comprehensive loadgen that exercises the full checkout flow (`labs/lab8/mixedload.yaml`)
- Structured chaos experiments with hypothesis-driven approach
- Documentation of system resilience weaknesses you found

---

## Setup

Apply the Lab 8 loadgen (exercises `/events`, `/reserve`, `/pay` together — the Lab 7 loadgen only hit read paths):

```bash
kubectl apply -f labs/lab8/mixedload.yaml
kubectl rollout status deployment/mixedload --timeout=60s
```

Wait 1-2 minutes so Prometheus has baseline data. Verify with a Prometheus query:

```bash
kubectl port-forward -n monitoring svc/prometheus 9091:9090 &
curl -s 'http://localhost:9091/api/v1/query?query=sum(rate(gateway_requests_total%5B1m%5D))' \
  | python3 -c "import sys,json;r=json.load(sys.stdin)['data']['result'];print('RPS:', r[0]['value'][1] if r else 'no data')"
```

> 💡 **Observation method.** You use Prometheus queries (`kubectl exec -n monitoring deployment/prometheus -- wget -qO- '…'`) or the Prometheus UI at `http://localhost:9091` after port-forwarding. The docker-compose Grafana from Lab 3 **cannot** scrape k3d pods; if you need dashboards inside the cluster, deploy Grafana alongside Prometheus (out of scope for this lab).

---

## Task 1 — Three Chaos Experiments (6 pts)

**Objective:** Design and execute 3 chaos experiments. For each: write hypothesis, inject failure, observe, compare with hypothesis, document findings.

### Experiment 1 — Pod Kill Under Load

**Write your hypothesis first** (before running the experiment):

```
HYPOTHESIS: "If I delete one gateway pod while traffic is flowing,
[expected behavior] will happen because [reason]."
```

**Execute:**

```bash
# Pick one victim
VICTIM=$(kubectl get pods -l app=gateway -o name | head -1)
echo "Killing $VICTIM at $(date +%H:%M:%S)"
kubectl delete "$VICTIM"
```

**Observe (document all of these):**

- How long until Kubernetes creates a replacement pod?
  ```bash
  kubectl get pods -l app=gateway -w        # Ctrl-C when 5/5 Running again
  ```
- Did any request fail during the transition?
  ```bash
  kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
    'http://localhost:9090/api/v1/query?query=sum(increase(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B3m%5D))'
  ```
- Did the per-pod request rate drop to zero during the gap, or was traffic picked up by the remaining 4 pods?
  ```bash
  kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
    'http://localhost:9090/api/v1/query?query=sum+by+(pod)+(rate(gateway_requests_total%5B1m%5D))'
  ```

**Compare:** Was your hypothesis correct? What surprised you?

### Experiment 2 — Payment Latency Injection

**Hypothesis:**

```
HYPOTHESIS: "If payments takes 2 seconds per request,
[expected behavior] will happen because [reason]."
```

**Execute** (no pod restart dance — `kubectl set env` triggers a rolling update):

```bash
kubectl set env deployment/payments PAYMENT_LATENCY_MS=2000
kubectl rollout status deployment/payments --timeout=30s
```

**Observe (wait ~60s for the rate window to fill):**

- Is the gateway returning 5xx? (2000ms < GATEWAY_TIMEOUT_MS of 5000ms — it should not)
  ```bash
  kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
    'http://localhost:9090/api/v1/query?query=sum(rate(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B1m%5D))/sum(rate(gateway_requests_total%5B1m%5D))'
  ```
- How does p99 latency change per endpoint? (Only `/pay` should spike; reads should be unaffected.)
  ```bash
  kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
    'http://localhost:9090/api/v1/query?query=histogram_quantile(0.99,+sum+by+(le,path)+(rate(gateway_request_duration_seconds_bucket%5B1m%5D)))'
  ```
- **Bonus observation:** push latency *beyond* the timeout to see the gateway protect itself.
  ```bash
  kubectl set env deployment/payments PAYMENT_LATENCY_MS=6000
  # /pay should now 504 after exactly GATEWAY_TIMEOUT_MS milliseconds
  ```

**Restore:** `kubectl set env deployment/payments PAYMENT_LATENCY_MS=0 && kubectl rollout status deployment/payments --timeout=30s`

### Experiment 3 — Redis Failure

**Hypothesis:**

```
HYPOTHESIS: "If Redis goes down, [expected behavior]
will happen because [reason]."
```

**Execute:**

```bash
kubectl scale deployment/redis --replicas=0
kubectl get pods -l app=redis -w    # wait until gone
```

**Observe:**

- Can users still list events? (list doesn't need Redis.)
- Can users reserve tickets? (reserve NEEDS Redis for the hold.)
- What does `/health` report now?

Quick checks from inside the cluster:

```bash
kubectl run chaos-probe --image=curlimages/curl:latest --rm -i --restart=Never --quiet --command -- \
  sh -c 'echo "GET /events:"; curl -s -o /dev/null -w "%{http_code} %{time_total}s\n" http://gateway:8080/events;
         echo "POST /reserve:"; curl -s -X POST -w "%{http_code} %{time_total}s\n" \
              -H "Content-Type: application/json" -d "{\"quantity\":1}" \
              http://gateway:8080/events/1/reserve;
         echo "GET /health:"; curl -s http://gateway:8080/health'
```

**Restore:** `kubectl scale deployment/redis --replicas=1 && kubectl wait --for=condition=Available deployment/redis --timeout=60s`

### Proof of work

**Paste into `submissions/lab8.md`:**

For EACH experiment (3 total):

1. Your hypothesis (written BEFORE running).
2. The command(s) you ran.
3. What you observed — Prometheus query output, `kubectl` output, HTTP responses. Include timestamps.
4. Comparison: hypothesis vs reality — what matched, what surprised you.
5. One sentence: "To improve resilience against this failure, I would..."

<details>
<summary>💡 Hints</summary>

- Write the hypothesis FIRST — the learning is in the surprise when reality differs.
- Keep `mixedload` running during all experiments (`kubectl get deployment mixedload`).
- Take note of wall-clock timestamps — you can correlate them with Prometheus time-series in the UI.
- "No impact" is a valid and interesting observation (K8s self-healing + Service load-balancing at work).
- Experiment 2 (latency) is the most educational — partial degradation is harder to detect than a dead service. Notice how only `/pay` p99 spikes while reads stay clean.
- After each experiment, wait for full recovery (health `healthy` + no elevated error-rate) before starting the next.

</details>

---

## Task 2 — Combined Failure Scenario (4 pts)

> ⏭️ This task is optional. Skipping it will not affect future labs.

**Objective:** Design and execute a more complex scenario with multiple simultaneous failures and identify the weakest link.

### 8.4: Design a combined scenario

Real incidents are usually multiple failures stacked. Pick one (or design your own):

- **Degraded dependencies:** payments 30% failure + 500ms latency AND DB connections capped at 3.
- **Cascade test:** kill Redis AND inject payment latency — does gateway degrade gracefully on both dimensions?
- **Capacity crunch:** scale `mixedload` to 5 replicas AND cap `DB_MAX_CONNS=2` — where does the system break first?

### 8.5: Execute and document

**Degraded dependencies example:**

```bash
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.3 PAYMENT_LATENCY_MS=500
kubectl set env deployment/events DB_MAX_CONNS=3
kubectl scale deployment/mixedload --replicas=3
kubectl rollout status deployment/payments --timeout=30s
kubectl rollout status deployment/events --timeout=30s
```

Let it run for 3-5 minutes. Sample these queries repeatedly:

```bash
# Error rate (ratio)
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(rate(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B1m%5D))/sum(rate(gateway_requests_total%5B1m%5D))'

# p99 latency per path
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=histogram_quantile(0.99,+sum+by+(le,path)+(rate(gateway_request_duration_seconds_bucket%5B1m%5D)))'
```

**Restore after you're done:**

```bash
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.0 PAYMENT_LATENCY_MS=0
kubectl set env deployment/events DB_MAX_CONNS=10
kubectl scale deployment/mixedload --replicas=2
```

**Paste into `submissions/lab8.md`:**

- Your scenario design (what + why).
- Observations over the 3-5 minute window — which golden signal reacted first?
- Which path shows the worst latency amplification? (`/events` vs `/events/{id}/reserve` vs `/pay`)
- Answer: "Which component was the weakest link? How would you make it more resilient?"

---

## Bonus Task — Resilience Improvement (2.5 pts)

> 🌟 For those who want extra challenge and experience.

**Objective:** Fix one weakness you found, then re-run the same experiment to prove it's better.

### B.1: Choose a weakness

From your experiments, pick the failure that had the worst impact. Examples discovered in real dry-runs:

- Reserve p99 latency shoots to 5+ seconds under `DB_MAX_CONNS=3` + mixed load (connection pool queueing).
- No alert fires for slow-but-successful payments (SLO breach hidden under "all 200 OK").
- Gateway retries a failed `/pay` instead of failing fast — amplifies downstream load.

### B.2: Implement a fix

Make a code or config change. Examples:

- Raise `DB_MAX_CONNS` and add `resources.requests` on the events pod so K8s schedules it with enough headroom.
- Add a latency SLO alert rule in Prometheus (`gateway_request_duration_seconds` p99 above threshold).
- Add a circuit breaker or idempotency key to gateway's call to payments.

### B.3: Re-run the experiment

Run the same experiment as before. Compare:

- **Before fix:** [impact metric]
- **After fix:** [impact metric]

**Paste into `submissions/lab8.md`:**

- Which weakness you chose.
- What you changed (config diff or code diff).
- Before-vs-after comparison with Prometheus query output or dashboard screenshot.
- One sentence: what the fix traded off.

---

## Cleanup

```bash
kubectl delete -f labs/lab8/mixedload.yaml
# Optional: leave the Argo Rollouts + Prometheus from Lab 7 running for Lab 9
```

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
- ✅ 3 experiments, each with: hypothesis, method, observations, comparison.
- ✅ Hypotheses written BEFORE executing (not retrofitted).
- ✅ Prometheus / `kubectl` output evidence for each experiment.
- ✅ At least one "I would improve…" statement per experiment.

### Task 2 (4 pts)
- ✅ Combined scenario with 2+ simultaneous failures.
- ✅ Observations with timestamps.
- ✅ Weakest link identified with explanation.

### Bonus Task (2.5 pts)
- ✅ Weakness chosen from experiments.
- ✅ Fix implemented (config or code diff).
- ✅ Before-vs-after comparison with evidence.

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
- [Prometheus query language docs](https://prometheus.io/docs/prometheus/latest/querying/basics/)

</details>

<details>
<summary>⚠️ Common Pitfalls</summary>

- **Forgot to apply mixedload.** The Lab 7 loadgen only hits `/events` and `/health`. Chaos injected into payments or Redis won't show impact unless `/reserve` and `/pay` are being called. Use `labs/lab8/mixedload.yaml`.
- **`kubectl set env` unsets values.** `kubectl set env deployment/payments PAYMENT_LATENCY_MS-` (note trailing `-`) removes the env var entirely. For this lab, set to `0` instead.
- **Restoring too fast.** Give Prometheus at least 60s to accumulate enough samples in its rate window, or your "before" observation will be noisy.
- **`rate()` returns empty for mutating paths.** If you don't run mixedload, there's no `/reserve` or `/pay` traffic, and the p99 histogram for those paths will show `NaN`.
- **p99 shows a small number right after the experiment starts.** Histogram buckets accumulate slowly — wait ~90s for the `[1m]` window to fill with the new data.
- **Scale-to-zero stays zero.** `kubectl scale deployment/redis --replicas=0` doesn't automatically restore — remember to scale back to 1 afterwards.

</details>
