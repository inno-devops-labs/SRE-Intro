# Lab 5 — CI/CD & GitOps

## Task 1

### .github/workflows/ci.yml and updated k8s manifests go in your fork

### Link to your GitHub Actions run (green check)

https://github.com/fifidadan/SRE-Intro/actions

https://github.com/fifidadan/SRE-Intro/actions/runs/27895106718

### Output of `gh api user/packages?package_type=container` showing pushed images

<img width="1910" height="550" alt="Снимок экрана 2026-06-21 135443" src="https://github.com/user-attachments/assets/7648303d-55d4-4338-9693-a3baa2f598d2" />


### Output of `argocd app get quickticket` showing Synced + Healthy

<img width="1911" height="1097" alt="Снимок экрана 2026-06-21 141945" src="https://github.com/user-attachments/assets/df8836e3-e7f3-4aab-8a9c-fe002821f327" />


### Output proving a Git change was synced (label, annotation, or image tag change visible in cluster)

```bash
axmed@honorfifi MINGW64 ~/SRE-Intro (feature/lab5)

$ kubectl get deployment gateway -o jsonpath='{.metadata.labels.version}'

v2
```

### Answer: "What happens if someone manually runs kubectl edit on a resource managed by ArgoCD?"

If someone manually edits a resource managed by ArgoCD using `kubectl edit`, ArgoCD will eventually revert the change back to what's defined in the Git repository.

This happens because ArgoCD continuously reconciles the cluster state with the desired state defined in Git. When it detects a drift (the cluster state differs from the Git state), it automatically corrects it by reapplying the manifests from Git.

---

## Task 2

### argocd app get showing Degraded after bad deploy


<img width="1642" height="1037" alt="Снимок экрана 2026-06-21 145010" src="https://github.com/user-attachments/assets/a092ad19-0558-4674-8a95-2932121d2132" />


### kubectl get pods showing ImagePullBackOff

```bash
axmed@honorfifi MINGW64 ~/SRE-Intro (feature/lab5)

$ kubectl get pods

NAME                        READY   STATUS             RESTARTS       AGE
events-5d68db599-4798p      0/1     ImagePullBackOff   0              14m
events-78696fcf65-hnmzt     1/1     Running            0              20m
gateway-5dcb68c5c4-vlncf    0/1     ErrImagePull       0              5s
gateway-7cd55d8774-md5gd    1/1     Running            0              20m
payments-7567cdd5c9-p8g2n   0/1     ImagePullBackOff   0              14m
payments-d7dc94485-thgm7    1/1     Running            0              20m
postgres-7c7ffc4b-49lgc     1/1     Running            5 (123m ago)   3d23h
redis-c46d5dffc-7jwwd       1/1     Running            5 (123m ago)   3d23h
```

### git log --oneline -3 showing the deploy + revert commits

```bash
axmed@honorfifi MINGW64 ~/SRE-Intro (feature/lab5)

$ git log --oneline -3

eda221a (HEAD -> feature/lab5, origin/feature/lab5) Revert "feat: deploy bad gateway version (does-not-exist)"
deaa533 feat: deploy bad gateway version (does-not-exist)
b4c5eaf feat: add version label to gateway Helm template
```

### argocd app get showing Healthy after revert

```bash
$ kubectl get pods

NAME                        READY   STATUS    RESTARTS       AGE
events-7b48495f8d-rxkt6     1/1     Running   0              80s
gateway-6c66d77c4f-ph868    1/1     Running   0              80s
payments-7897b4855b-9wndf   1/1     Running   0              80s
postgres-7c7ffc4b-49lgc     1/1     Running   5 (173m ago)   4d
redis-c46d5dffc-7jwwd       1/1     Running   5 (173m ago)   4d
```


<img width="1905" height="1091" alt="Снимок экрана 2026-06-21 154100" src="https://github.com/user-attachments/assets/3c5c2db5-ef93-41e0-84b9-18351a39dbd2" />


### Answer: "How long from git revert + push to pods being healthy again?"

After running git revert HEAD and pushing the revert commit, ArgoCD detected the change and synced the application. The entire process took approximately 2-3 minutes:

* git revert + git push: ~10 seconds
* ArgoCD polling interval (default 3 minutes): detects the change and starts sync
* Pods pulling the correct image and becoming healthy: ~30-60 seconds

Total time: Around 2-3 minutes from git revert to pods being healthy again.
