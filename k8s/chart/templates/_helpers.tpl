{{/*
Common labels. `app: <name>` is kept as the selector label so that the kubectl
one-liners used throughout the course (`kubectl get pod -l app=gateway`) work
identically against a Helm release and against the raw k8s/ manifests.
*/}}
{{- define "quickticket.labels" -}}
app: {{ .name }}
app.kubernetes.io/name: {{ .name }}
app.kubernetes.io/part-of: quickticket
app.kubernetes.io/managed-by: {{ .root.Release.Service }}
helm.sh/chart: {{ .root.Chart.Name }}-{{ .root.Chart.Version }}
{{- end -}}

{{/*
Prometheus scrape-by-annotation block for the application services.
*/}}
{{- define "quickticket.metricsAnnotations" -}}
{{- if .root.Values.metrics.annotations }}
annotations:
  prometheus.io/scrape: "true"
  prometheus.io/port: {{ .port | quote }}
  prometheus.io/path: "/metrics"
{{- end }}
{{- end -}}

{{/*
Probes for an application service: HTTP /health readiness (dependency aware)
plus a TCP liveness probe (process aware only).
*/}}
{{- define "quickticket.probes" -}}
{{- if .root.Values.probes.enabled }}
readinessProbe:
  httpGet:
    path: /health
    port: {{ .port }}
  periodSeconds: {{ .root.Values.probes.readiness.periodSeconds }}
  failureThreshold: {{ .root.Values.probes.readiness.failureThreshold }}
livenessProbe:
  tcpSocket:
    port: {{ .port }}
  initialDelaySeconds: {{ .root.Values.probes.liveness.initialDelaySeconds }}
  periodSeconds: {{ .root.Values.probes.liveness.periodSeconds }}
  failureThreshold: {{ .root.Values.probes.liveness.failureThreshold }}
{{- end }}
{{- end -}}
