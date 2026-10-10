{{- define "quickticket.application" }}
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ .name }}
  labels:
    app: {{ .name }}
    app.kubernetes.io/name: {{ .name }}
    app.kubernetes.io/instance: {{ .root.Release.Name }}
    app.kubernetes.io/managed-by: {{ .root.Release.Service }}
spec:
  replicas: {{ .component.replicas }}
  selector:
    matchLabels:
      app: {{ .name }}
  template:
    metadata:
      labels:
        app: {{ .name }}
        app.kubernetes.io/name: {{ .name }}
        app.kubernetes.io/instance: {{ .root.Release.Name }}
    spec:
      containers:
        - name: {{ .name }}
          image: {{ .component.image | quote }}
          imagePullPolicy: {{ .component.imagePullPolicy }}
          ports:
            - name: http
              containerPort: {{ .component.port }}
          env:
{{- range $key, $value := .env }}
            - name: {{ $key }}
              value: {{ $value | quote }}
{{- end }}
          livenessProbe:
            httpGet:
              path: /health
              port: {{ .component.port }}
            initialDelaySeconds: {{ .root.Values.probes.liveness.initialDelaySeconds }}
            periodSeconds: {{ .root.Values.probes.liveness.periodSeconds }}
            failureThreshold: {{ .root.Values.probes.liveness.failureThreshold }}
          readinessProbe:
            httpGet:
              path: /health
              port: {{ .component.port }}
            initialDelaySeconds: {{ .root.Values.probes.readiness.initialDelaySeconds }}
            periodSeconds: {{ .root.Values.probes.readiness.periodSeconds }}
            failureThreshold: {{ .root.Values.probes.readiness.failureThreshold }}
          resources:
{{ toYaml .root.Values.resources | indent 12 }}
---
apiVersion: v1
kind: Service
metadata:
  name: {{ .name }}
  labels:
    app: {{ .name }}
    app.kubernetes.io/instance: {{ .root.Release.Name }}
spec:
  type: ClusterIP
  selector:
    app: {{ .name }}
  ports:
    - name: http
      port: {{ .component.port }}
      targetPort: {{ .component.port }}
{{- end }}
