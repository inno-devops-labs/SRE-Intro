# Lab 3 — Monitoring, Observability & SLOs
## Proof of work by Viktoriya Yurina B24-CBS-01

### Task 1 — Configure Monitoring & Build Dashboard 
#### Output of compose ps showing all 7 services
```
┌──(shersh㉿kali)-[~/SRE-Intro/app]
└─$ sudo docker-compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml ps
NAME               IMAGE                     COMMAND                  SERVICE      CREATED              STATUS                    PORTS
app-events-1       app-events                "uvicorn main:app --…"   events       23 minutes ago       Up 23 minutes             0.0.0.0:8081->8081/tcp, :::8081->8081/tcp
app-gateway-1      app-gateway               "uvicorn main:app --…"   gateway      23 minutes ago       Up 23 minutes             0.0.0.0:3080->8080/tcp, [::]:3080->8080/tcp
app-grafana-1      grafana/grafana:13.0.1    "/run.sh"                grafana      About a minute ago   Up About a minute         0.0.0.0:3000->3000/tcp, :::3000->3000/tcp
app-payments-1     app-payments              "uvicorn main:app --…"   payments     23 minutes ago       Up 23 minutes             0.0.0.0:8082->8082/tcp, :::8082->8082/tcp
app-postgres-1     postgres:17-alpine        "docker-entrypoint.s…"   postgres     6 days ago           Up 23 minutes (healthy)   0.0.0.0:5432->5432/tcp, :::5432->5432/tcp
app-prometheus-1   prom/prometheus:v3.11.2   "/bin/prometheus --c…"   prometheus   About a minute ago   Up About a minute         0.0.0.0:9090->9090/tcp, :::9090->9090/tcp
app-redis-1        redis:7-alpine            "docker-entrypoint.s…"   redis        6 days ago           Up 23 minutes (healthy)   0.0.0.0:6379->6379/tcp, :::6379->6379/tcp
```

#### Prometheus targets output (all 3 up)
```
┌──(shersh㉿kali)-[~/SRE-Intro/app]
└─$ curl -s http://localhost:9090/api/v1/targets | python3 -c "
import sys, json
for t in json.load(sys.stdin)['data']['activeTargets']:
    print(f\"{t['labels']['job']:12} {t['health']:8} {t['scrapeUrl']}\")
"
app-events-1 up       http://events:8081/metrics
app-gateway-1 up       http://gateway:8080/metrics
app-payments-1 up       http://payments:8082/metrics
```

#### Custom metrics list
```
┌──(shersh㉿kali)-[~/SRE-Intro/app]
└─$ curl -s http://localhost:9090/api/v1/label/__name__/values | python3 -c "
import sys, json
for n in json.load(sys.stdin)['data']:
    if any(x in n for x in ['gateway_', 'events_', 'payments_']):
        print(n)
"
events_db_pool_size
events_orders_created
events_orders_total
events_reservations_active
prometheus_sd_kubernetes_events_total
```

#### PromQL query output (request rate)
```
┌──(shersh㉿kali)-[~/SRE-Intro/app]
└─$ curl -s --data-urlencode 'query=sum(rate(gateway_requests_total[5m]))' \
  http://localhost:9090/api/v1/query | python3 -c "
import sys, json
r = json.load(sys.stdin)
print(f\"Request rate: {float(r['data']['result'][0]['value'][1]):.2f} req/s\")"
Request rate: 0.29 req/s
```

#### PromQL queries you used for Latency and Saturation panels
Latency:
```
histogram_quantile(0.50, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
histogram_quantile(0.95, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
histogram_quantile(0.99, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
```
Saturation:
```
events_db_pool_size
```

#### Dashboard observations: normal traffic vs payments failure
During normal operation, the dashboard showed stable request traffic, zero error rate, and low latency across all percentiles. All services were healthy and responding correctly.
```
┌──(shersh㉿kali)-[~/SRE-Intro/app]
└─$ ./loadgen/run.sh 5 20
QuickTicket Load Generator
Target: http://localhost:3080 | RPS: 5 | Duration: 20s
---
sl[10s] requests=40 success=40 fail=0 error_rate=0%
e[10s] requests=41 success=41 fail=0 error_rate=0%
ep[10s] requests=42 success=42 fail=0 error_rate=0%
[10s] requests=43 success=43 fail=0 error_rate=0%

---
Done. total=81 success=81 fail=0 error_rate=0%
```

After the payments service was killed, the error rate increased to approximately 5–5%, indicating failed payment requests. Latency (especially p95 and p99) also increased, while request traffic continued because requests were still reaching the gateway. Service health showed that the payments service was no longer operating normally.
```
┌──(shersh㉿kali)-[~/SRE-Intro/app]
└─$ ./loadgen/run.sh 5 60 &
sleep 15
[1] 29542
QuickTicket Load Generator
Target: http://localhost:3080 | RPS: 5 | Duration: 60s
---
[10s] requests=40 success=40 fail=0 error_rate=0%
[10s] requests=41 success=41 fail=0 error_rate=0%
[10s] requests=42 success=42 fail=0 error_rate=0%
[10s] requests=43 success=43 fail=0 error_rate=0%
                                                                                                                                              
┌──(shersh㉿kali)-[~/SRE-Intro/app]
└─$ [20s] requests=82 success=82 fail=0 error_rate=0%
[20s] requests=83 success=83 fail=0 error_rate=0%
[20s] requests=84 success=84 fail=0 error_rate=0%
[20s] requests=85 success=85 fail=0 error_rate=0%
[30s] requests=123 success=123 fail=0 error_rate=0%
[30s] requests=124 success=124 fail=0 error_rate=0%
[30s] requests=125 success=125 fail=0 error_rate=0%
[30s] requests=126 success=126 fail=0 error_rate=0%
[40s] requests=165 success=163 fail=2 error_rate=1.2%
[40s] requests=166 success=164 fail=2 error_rate=1.2%
[40s] requests=167 success=165 fail=2 error_rate=1.1%
[40s] requests=168 success=166 fail=2 error_rate=1.1%
[50s] requests=207 success=199 fail=8 error_rate=3.8%
[50s] requests=208 success=200 fail=8 error_rate=3.8%
[50s] requests=209 success=201 fail=8 error_rate=3.8%
[50s] requests=210 success=202 fail=8 error_rate=3.8%
---
Done. total=248 success=235 fail=13 error_rate=5.2%

[1]  + done       ./loadgen/run.sh 5 60
┌──(shersh㉿kali)-[~/SRE-Intro/app]
└─$ ./loadgen/run.sh 5 60 &
sleep 15
[1] 34086
QuickTicket Load Generator
Target: http://localhost:3080 | RPS: 5 | Duration: 60s
---
[10s] requests=41 success=40 fail=1 error_rate=2.4%
[10s] requests=42 success=41 fail=1 error_rate=2.3%
[10s] requests=43 success=42 fail=1 error_rate=2.3%
[10s] requests=44 success=43 fail=1 error_rate=2.2%
                                                                                                                                            
┌──(shersh㉿kali)-[~/SRE-Intro/app]
└─$ [20s] requests=82 success=79 fail=3 error_rate=3.6%
[20s] requests=83 success=80 fail=3 error_rate=3.6%
[20s] requests=84 success=81 fail=3 error_rate=3.5%
[20s] requests=85 success=82 fail=3 error_rate=3.5%
[30s] requests=124 success=117 fail=7 error_rate=5.6%
[30s] requests=125 success=118 fail=7 error_rate=5.6%
[30s] requests=126 success=119 fail=7 error_rate=5.5%
[30s] requests=127 success=120 fail=7 error_rate=5.5%
[40s] requests=166 success=154 fail=12 error_rate=7.2%
[40s] requests=167 success=155 fail=12 error_rate=7.1%
[40s] requests=168 success=156 fail=12 error_rate=7.1%
[40s] requests=169 success=157 fail=12 error_rate=7.1%
[50s] requests=209 success=194 fail=15 error_rate=7.1%
[50s] requests=210 success=195 fail=15 error_rate=7.1%
[50s] requests=211 success=196 fail=15 error_rate=7.1%
[50s] requests=212 success=197 fail=15 error_rate=7.0%
---
Done. total=250 success=231 fail=19 error_rate=7.6%
```

#### Answer: "Which golden signal showed the failure first? How long after killing payments?"
The Error Rate golden signal showed the failure first. The increase became visible approximately 15–30 seconds after killing the payments service, which matches the Prometheus scrape interval and metric collection delay.

### Task 2 — Define SLOs & Recording Rules
#### SLI/SLO definitions with error budget math
SLI 1 (Availability): Percentage of gateway requests that do not return 5xx errors.\
SLO 1: 99.5% availability measured over a rolling 7-day window.\
SLI 2 (Latency): Percentage of gateway requests completed in less than 500 ms.\
SLO 2: 95% of requests should complete within 500 ms.\

Error budget math:\
With approximately 1000 requests per day, the system receives about 7000 requests per week.\
For an availability SLO of 99.5%, the error budget is 0.5% of requests.\
7000 × 0.005 = 35\
Thus, the error budget allows up to 35 failed requests per week.

#### Rules loaded output
```
gateway:sli_availability:ratio_rate5m        = ok
gateway:sli_latency_500ms:ratio_rate5m       = ok
gateway:error_budget_burn_rate:ratio_rate5m  = ok
```
Recorded metrics:
```
gateway:sli_availability:ratio_rate5m = 0.9191919191919192
gateway:sli_latency_500ms:ratio_rate5m = 1
gateway:error_budget_burn_rate:ratio_rate5m = 16.16161616161614
```

#### SLO gauge observation during failure
During the failure experiment, the SLO gauge dropped from ~100% to about 91.92% when the payments service was stopped. This fell well below the 99.5% SLO threshold, clearly indicating an availability violation, and the burn rate spiked to roughly 16.16, showing fast error budget consumption.
