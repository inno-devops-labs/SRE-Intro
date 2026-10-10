# Lab 7 — Progressive Delivery: Canary Deployments

## Task 1 — Manual Canary

For this task I installed Argo Rollouts.

I changed `gateway` from Deployment to Rollout and used canary strategy.

I started canary deployment with 20% traffic.

After checking that everything worked, I did manual promotion.

I also tested a bad version and used **abort**. The rollback was very fast.

**What I learned:** Abort is much faster than using `git revert` like in Lab 5.

---

## Task 2 — Multi-step Canary

I used a multi-step canary strategy:

```yaml
steps:
  - setWeight: 20
  - pause: {duration: 60s}
  - setWeight: 40
  - pause: {duration: 60s}
  - setWeight: 60
  - pause: {duration: 60s}
  - setWeight: 80
  - pause: {duration: 30s}
  - setWeight: 100
```

I watched rollout progress using:

```bash
kubectl get rollout gateway
kubectl get pods
```

The traffic slowly moved to the new version step by step.

---

## Bonus Task — Automated Canary Analysis

I created an AnalysisTemplate called `gateway-error-rate`.

After that I added analysis to the Rollout.

I tested both auto-promote and auto-abort.

The most interesting thing was automatic rollback. If the new version had problems, Rollouts stopped it and returned to the old version automatically.

---

## Final Thoughts

In this lab I learned:

* Canary deployments
* Manual promotion
* Manual abort
* Multi-step rollout strategy
* Automated analysis with Argo Rollouts

I think this is a very useful way to deploy applications more safely in production.
