{{- define "quickticket.labels" -}}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/instance: {{ .Release.Name }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end -}}

{{- define "quickticket.resources" -}}
resources:
  {{- toYaml .Values.resources | nindent 2 }}
{{- end -}}

{{- define "quickticket.httpProbes" -}}
livenessProbe:
  httpGet:
    path: {{ .liveness }}
    port: {{ .port }}
  initialDelaySeconds: 10
  periodSeconds: 10
  failureThreshold: 3
readinessProbe:
  httpGet:
    path: /health
    port: {{ .port }}
  periodSeconds: 5
  failureThreshold: 2
{{- end -}}

{{- define "quickticket.service" -}}
apiVersion: v1
kind: Service
metadata:
  name: {{ .name }}
  labels:
    app: {{ .name }}
    {{- include "quickticket.labels" .root | nindent 4 }}
spec:
  type: ClusterIP
  selector:
    app: {{ .name }}
  ports:
    - port: {{ .port }}
      targetPort: {{ .port }}
{{- end -}}
