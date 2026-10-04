# Lab 8 — Chaos Engineering: Break Things on Purpose

## Environment and method

Experiments ran on the local `quickticket` k3d cluster, namespace `default`,
with the five-replica gateway Rollout from Lab 7. In-cluster Prometheus
scraped gateway pods every five seconds. All timestamps below are UTC.

The provided `labs/lab8/mixedload.yaml` exercised event listing, reservation,
and payment sequentially. It normally ran with two replicas; the combined
scenario used three. This is a closed-loop workload: slower checkout reduces
the rate at which subsequent requests are generated.

ArgoCD auto-sync/self-heal was temporarily disabled before fault injection
and restored after cleanup. The live application code was saved from the
containers because the deployed Lab 5 image could differ from course main.

Event 1's local test inventory was temporarily increased from 100 to
1,000,000 so successful checkout traffic would not exhaust the event during
the measurement windows. The original inventory was restored after stopping
load. Generated orders were retained; this was not an external customer workload.

### Baseline

The final baseline sample had approximately 16.8 gateway requests per second,
zero observed 5xx ratio, and successful traffic on all three checkout paths.

```json
{
  "timestamp": "2026-10-04T12:11:47.651886+00:00",
  "rps_by_path": [
    {
      "metric": {
        "path": "/events"
      },
      "value": [
        1791115908.297,
        "5.563777864451229"
      ]
    },
    {
      "metric": {
        "path": "/health"
      },
      "value": [
        1791115908.297,
        "0"
      ]
    },
    {
      "metric": {
        "path": "/events/{id}/reserve"
      },
      "value": [
        1791115908.297,
        "5.6000985265457865"
      ]
    },
    {
      "metric": {
        "path": "/reserve/{id}/pay"
      },
      "value": [
        1791115908.297,
        "5.618310758287433"
      ]
    }
  ],
  "status_rps": [
    {
      "metric": {
        "status": "200"
      },
      "value": [
        1791115908.464,
        "16.78218714928445"
      ]
    }
  ],
  "error_ratio": [
    {
      "metric": {},
      "value": [
        1791115908.679,
        "0"
      ]
    }
  ],
  "p99_by_path": [
    {
      "metric": {
        "path": "/events"
      },
      "value": [
        1791115908.778,
        "0.024207689768125578"
      ]
    },
    {
      "metric": {
        "path": "/health"
      },
      "value": [
        1791115908.778,
        "NaN"
      ]
    },
    {
      "metric": {
        "path": "/events/{id}/reserve"
      },
      "value": [
        1791115908.778,
        "0.06837450230477077"
      ]
    },
    {
      "metric": {
        "path": "/reserve/{id}/pay"
      },
      "value": [
        1791115908.778,
        "0.06727505554560033"
      ]
    }
  ]
}
```

```json
{"status":"healthy","checks":{"events":"ok","payments":"ok","circuit_payments":"CLOSED"}}
```

### Prometheus observation queries

Rates were calculated before aggregation, preserving per-series reset handling.
The error numerator falls back to zero when no 5xx series exists; the
denominator remains strict. An absent result does not establish zero errors.
Histogram p99 is a bucket-based estimate, not an exact request duration.

```promql
sum by(pod)(rate(gateway_requests_total[1m]))

sum by(path)(rate(gateway_requests_total[1m]))

(sum(rate(gateway_requests_total{status=~"5.."}[1m])) or on() vector(0))
/
sum(rate(gateway_requests_total[1m]))

histogram_quantile(
  0.99,
  sum by(le,path)(rate(gateway_request_duration_seconds_bucket[1m]))
)
```

## Task 1 — Three chaos experiments

### Experiment 1 — Gateway pod kill under load

**Hypothesis recorded at:** `2026-10-04T12:14:41.296161+00:00`

If one gateway pod is deleted while traffic flows, the remaining four ready pods should serve new requests and the ReplicaSet should create a replacement. A short interruption is possible for in-flight requests, so zero client failures is a prediction to test, not a guarantee.

```text
kubectl --context k3d-quickticket -n default delete pod gateway-58bd6b745f-5j7p2 --wait=false
```

```json
{
  "injected_at": "2026-10-04T12:14:42.362423+00:00",
  "victim": "gateway-58bd6b745f-5j7p2",
  "replacement_created": {
    "name": "gateway-58bd6b745f-pc5hf",
    "api_creation_timestamp": "2026-10-04T12:14:43Z",
    "observed_at": "2026-10-04T12:14:44.245171+00:00",
    "elapsed_seconds": 1.55
  },
  "five_ready_restored": {
    "observed_at": "2026-10-04T12:14:52.430525+00:00",
    "elapsed_seconds": 9.93,
    "ready_pods": 5
  },
  "control_events_response_counts": {
    "200": 67
  },
  "observation_finished_at": "2026-10-04T12:16:23.377710+00:00",
  "limitations": "Control probes exercise /events; mixedload continues checkout traffic. Prometheus cannot capture errors that never reached gateway or final counter changes on a deleted pod after its last scrape."
}
```

```text
NAME                       READY   STATUS    RESTARTS   AGE    IP            NODE                       NOMINATED NODE   READINESS GATES
gateway-58bd6b745f-9dxjd   1/1     Running   0          18m    10.42.0.97    k3d-quickticket-server-0   <none>           <none>
gateway-58bd6b745f-f9pj2   1/1     Running   0          25m    10.42.0.91    k3d-quickticket-server-0   <none>           <none>
gateway-58bd6b745f-lq9g5   1/1     Running   0          22m    10.42.0.95    k3d-quickticket-server-0   <none>           <none>
gateway-58bd6b745f-pc5hf   1/1     Running   0          100s   10.42.0.100   k3d-quickticket-server-0   <none>           <none>
gateway-58bd6b745f-zl9qw   1/1     Running   0          23m    10.42.0.92    k3d-quickticket-server-0   <none>           <none>
```

Sampled per-pod rates during replacement:

```json
[
  {
    "timestamp": "2026-10-04T12:14:42.587482+00:00",
    "rps_by_pod": [
      {
        "metric": {
          "pod": "gateway-58bd6b745f-f9pj2"
        },
        "value": [
          1791116082.666,
          "3.254722984890085"
        ]
      },
      {
        "metric": {
          "pod": "gateway-58bd6b745f-zl9qw"
        },
        "value": [
          1791116082.666,
          "3.236304794458283"
        ]
      },
      {
        "metric": {
          "pod": "gateway-58bd6b745f-5j7p2"
        },
        "value": [
          1791116082.666,
          "3.327454224775897"
        ]
      },
      {
        "metric": {
          "pod": "gateway-58bd6b745f-lq9g5"
        },
        "value": [
          1791116082.666,
          "3.272905794861538"
        ]
      },
      {
        "metric": {
          "pod": "gateway-58bd6b745f-9dxjd"
        },
        "value": [
          1791116082.666,
          "3.2909689267077584"
        ]
      }
    ]
  },
  {
    "timestamp": "2026-10-04T12:14:59.039169+00:00",
    "rps_by_pod": [
      {
        "metric": {
          "pod": "gateway-58bd6b745f-f9pj2"
        },
        "value": [
          1791116099.262,
          "3.436363636363636"
        ]
      },
      {
        "metric": {
          "pod": "gateway-58bd6b745f-zl9qw"
        },
        "value": [
          1791116099.262,
          "3.6547448042620507"
        ]
      },
      {
        "metric": {
          "pod": "gateway-58bd6b745f-5j7p2"
        },
        "value": [
          1791116099.262,
          "2.4052633881694083"
        ]
      },
      {
        "metric": {
          "pod": "gateway-58bd6b745f-lq9g5"
        },
        "value": [
          1791116099.262,
          "3.290968926707759"
        ]
      },
      {
        "metric": {
          "pod": "gateway-58bd6b745f-9dxjd"
        },
        "value": [
          1791116099.262,
          "3.5992801439712054"
        ]
      }
    ]
  },
  {
    "timestamp": "2026-10-04T12:15:14.590391+00:00",
    "rps_by_pod": [
      {
        "metric": {
          "pod": "gateway-58bd6b745f-f9pj2"
        },
        "value": [
          1791116115.275,
          "3.5636363636363635"
        ]
      },
      {
        "metric": {
          "pod": "gateway-58bd6b745f-zl9qw"
        },
        "value": [
          1791116115.275,
          "3.5454545454545454"
        ]
      },
      {
        "metric": {
          "pod": "gateway-58bd6b745f-5j7p2"
        },
        "value": [
          1791116115.275,
          "1.533841460975317"
        ]
      },
      {
        "metric": {
          "pod": "gateway-58bd6b745f-lq9g5"
        },
        "value": [
          1791116115.275,
          "3.1636363636363636"
        ]
      },
      {
        "metric": {
          "pod": "gateway-58bd6b745f-9dxjd"
        },
        "value": [
          1791116115.275,
          "3.8181123979564005"
        ]
      },
      {
        "metric": {
          "pod": "gateway-58bd6b745f-pc5hf"
        },
        "value": [
          1791116115.275,
          "1.3060633333333336"
        ]
      }
    ]
  }
]
```

**Comparison:** the replacement was observed after 1.55 seconds and five
non-terminating Ready pods were restored after 9.93 seconds. All 67 control
`/events` requests returned HTTP 200; sampled gateway 5xx ratio remained zero.
The remaining ready pods continued receiving traffic while replacement occurred.

A one-minute rate can retain the deleted pod's historical traffic; it cannot
show an instantaneous zero at deletion. Prometheus may also miss a deleted
pod's final counter changes after its last scrape. Control probes covered
reads, while mixedload continued checkout; these observations do not prove
that every in-flight checkout request succeeded.

**To improve resilience against this failure, I would** spread gateway
replicas across multiple nodes and use graceful connection draining, because
five replicas on one node do not protect against node loss.

### Experiment 2 — Payment latency injection

**Hypothesis recorded at:** `2026-10-04T12:19:14.078264+00:00`

If payments adds 2000ms to charge requests while the gateway timeout is 5000ms, payment latency should rise without timeout-induced 5xx. Read and reservation handler latency should remain near baseline. Overall request throughput may fall because mixedload performs checkout steps sequentially.

```text
kubectl set env deployment/payments PAYMENT_LATENCY_MS=2000
kubectl rollout status deployment/payments
Observe for at least 90 seconds
Restore PAYMENT_LATENCY_MS=0 and verify recovery
```

```json
{
  "hypothesis_written_at": "2026-10-04T12:19:14.078264+00:00",
  "injection_started_at": "2026-10-04T12:19:15.394608+00:00",
  "injected_deployment_ready_at": "2026-10-04T12:19:23.933286+00:00",
  "restore_started_at": "2026-10-04T12:21:20.296792+00:00",
  "restored_deployment_ready_at": "2026-10-04T12:21:29.879898+00:00"
}
```

| UTC timestamp | 5xx ratio | Events p99 (s) | Reserve p99 (s) | Pay p99 (s) | Total RPS |
|---|---:|---:|---:|---:|---:|
| 2026-10-04T12:19:23.934371+00:00 | 0.0000 | 0.0376 | 0.0997 | 0.0655 | 16.510 |
| 2026-10-04T12:19:39.929452+00:00 | 0.0000 | 0.0354 | 0.1335 | 2.2850 | 12.600 |
| 2026-10-04T12:19:55.652401+00:00 | 0.0000 | 0.0365 | 0.1720 | 2.4591 | 8.636 |
| 2026-10-04T12:20:12.368937+00:00 | 0.0000 | 0.0239 | 0.0929 | 5.3000 | 4.691 |
| 2026-10-04T12:20:28.092293+00:00 | 0.0000 | 0.0238 | 0.0380 | 2.4850 | 2.600 |
| 2026-10-04T12:20:43.764176+00:00 | 0.0000 | 0.0233 | 0.0380 | 2.4850 | 2.546 |
| 2026-10-04T12:20:59.554630+00:00 | 0.0000 | 0.0637 | 0.0233 | 2.4850 | 2.491 |
| 2026-10-04T12:21:15.485443+00:00 | 0.0000 | 0.0882 | 0.0233 | 2.4850 | 2.509 |

Control HTTP responses:

```json
[
  {
    "timestamp": "2026-10-04T12:21:17.760008+00:00",
    "path": "/events",
    "exit_code": 0,
    "response_summary": "HTTP_STATUS=200 TOTAL_SECONDS=0.177362",
    "error_body": null
  },
  {
    "timestamp": "2026-10-04T12:21:17.906671+00:00",
    "path": "/events/1/reserve",
    "exit_code": 0,
    "response_summary": "HTTP_STATUS=200 TOTAL_SECONDS=0.009945",
    "error_body": null
  },
  {
    "timestamp": "2026-10-04T12:21:20.086972+00:00",
    "path": "/reserve/dd46252c-2fd3-4720-9d57-4a7ca9c7d49a/pay",
    "exit_code": 0,
    "response_summary": "HTTP_STATUS=200 TOTAL_SECONDS=2.072788",
    "error_body": null
  },
  {
    "timestamp": "2026-10-04T12:21:20.192017+00:00",
    "path": "/health",
    "exit_code": 0,
    "response_summary": "HTTP_STATUS=200 TOTAL_SECONDS=0.006532",
    "error_body": "{\"status\":\"healthy\",\"checks\":{\"events\":\"ok\",\"payments\":\"ok\",\"circuit_payments\":\"CLOSED\"}}"
  }
]
```

Prometheus alert rules during the original experiment:

```json
{
  "status": "success",
  "data": {
    "groups": []
  }
}
```

**Comparison:** the direct payment completed with HTTP 200 in 2.072788 seconds.
The established payment p99 estimate was approximately 2.485 seconds;
read and reservation latency remained much lower. Sampled 5xx ratio was zero.
Throughput fell to approximately 2.5 requests per second because both load
workers waited for checkout to complete before issuing the next sequence.

One transitional p99 estimate reached approximately 5.30 seconds. This is
recorded as a histogram observation, not evidence that the injected delay
was exactly 5.30 seconds. The direct timing and later windows confirmed the
expected two-second payment delay.

The system was healthy according to dependency health checks, and Prometheus
had no alert rules: slow successful payments were not detected by an alert.

**To improve resilience against this failure, I would** add a payment-path
latency SLO alert so successful but slow checkout is visible to responders.

Recovery sample and health:

```json
{
  "timestamp": "2026-10-04T12:23:08.649589+00:00",
  "rps_by_path": [
    {
      "metric": {
        "path": "/events"
      },
      "value": [
        1791116589.28,
        "5.527359673033919"
      ]
    },
    {
      "metric": {
        "path": "/health"
      },
      "value": [
        1791116589.28,
        "0"
      ]
    },
    {
      "metric": {
        "path": "/events/{id}/reserve"
      },
      "value": [
        1791116589.28,
        "5.527360664919721"
      ]
    },
    {
      "metric": {
        "path": "/reserve/{id}/pay"
      },
      "value": [
        1791116589.28,
        "5.563722317728133"
      ]
    }
  ],
  "status_rps": [
    {
      "metric": {
        "status": "200"
      },
      "value": [
        1791116589.37,
        "16.618442655681772"
      ]
    },
    {
      "metric": {
        "status": "502"
      },
      "value": [
        1791116589.37,
        "0"
      ]
    },
    {
      "metric": {
        "status": "504"
      },
      "value": [
        1791116589.37,
        "0"
      ]
    }
  ],
  "error_ratio": [
    {
      "metric": {},
      "value": [
        1791116589.449,
        "0"
      ]
    }
  ],
  "p99_by_path": [
    {
      "metric": {
        "path": "/reserve/{id}/pay"
      },
      "value": [
        1791116589.532,
        "0.07058339494573312"
      ]
    },
    {
      "metric": {
        "path": "/events"
      },
      "value": [
        1791116589.532,
        "0.03300041969557401"
      ]
    },
    {
      "metric": {
        "path": "/health"
      },
      "value": [
        1791116589.532,
        "NaN"
      ]
    },
    {
      "metric": {
        "path": "/events/{id}/reserve"
      },
      "value": [
        1791116589.532,
        "0.06199964091760278"
      ]
    }
  ],
  "pay_observations_1m": [
    {
      "metric": {},
      "value": [
        1791116590.27,
        "337.09705809575445"
      ]
    }
  ]
}
```

```json
{
  "timestamp": "2026-10-04T12:23:10.401419+00:00",
  "path": "/health",
  "exit_code": 0,
  "response": "{\"status\":\"healthy\",\"checks\":{\"events\":\"ok\",\"payments\":\"ok\",\"circuit_payments\":\"CLOSED\"}}\nHTTP_STATUS=200 TOTAL_SECONDS=0.009442",
  "stderr": ""
}
```

### Experiment 3 — Redis unavailable

**Hypothesis recorded at:** `2026-10-04T12:25:50.472353+00:00`

If Redis is scaled to zero, new reservations should fail because reservation holds require Redis. Event listing does not intrinsically require Redis, but dependency-sensitive Events readiness or liveness may remove Events endpoints or restart the service, also disrupting reads through the gateway. Health should report degraded dependencies.

```text
kubectl scale deployment/redis --replicas=0
Observe reads, reservations, health, pod readiness and metrics
kubectl scale deployment/redis --replicas=1
Verify dependency and application recovery before the next experiment
```

Events probes before injection:

```json
{
  "readinessProbe": {
    "failureThreshold": 2,
    "httpGet": {
      "path": "/health",
      "port": 8081,
      "scheme": "HTTP"
    },
    "initialDelaySeconds": 5,
    "periodSeconds": 5,
    "successThreshold": 1,
    "timeoutSeconds": 1
  },
  "livenessProbe": {
    "failureThreshold": 3,
    "httpGet": {
      "path": "/health",
      "port": 8081,
      "scheme": "HTTP"
    },
    "initialDelaySeconds": 10,
    "periodSeconds": 10,
    "successThreshold": 1,
    "timeoutSeconds": 1
  }
}
```

```json
{
  "hypothesis_written_at": "2026-10-04T12:25:50.472353+00:00",
  "injection_started_at": "2026-10-04T12:25:50.994143+00:00",
  "restore_started_at": "2026-10-04T12:27:46.733541+00:00",
  "redis_deployment_ready_at": "2026-10-04T12:27:54.373192+00:00",
  "application_healthy_at": "2026-10-04T12:28:13.297768+00:00"
}
```

| UTC timestamp | 5xx ratio | Events p99 (s) | Reserve p99 (s) | Pay p99 (s) | Total RPS |
|---|---:|---:|---:|---:|---:|
| 2026-10-04T12:25:59.506641+00:00 | 0.0000 | 0.0921 | 5.3167 | 0.0669 | 14.927 |
| 2026-10-04T12:26:18.964104+00:00 | 0.0228 | 1.0975 | 6.9778 | 0.0553 | 9.187 |
| 2026-10-04T12:26:35.631514+00:00 | 0.3756 | 3.3626 | 7.1278 | 0.0478 | 5.621 |
| 2026-10-04T12:26:55.720076+00:00 | 0.9807 | 4.0584 | 7.0250 | no samples | 3.699 |
| 2026-10-04T12:27:12.701173+00:00 | 1.0000 | 3.8750 | 4.5250 | no samples | 4.897 |
| 2026-10-04T12:27:29.566216+00:00 | 1.0000 | 3.9833 | 4.8437 | no samples | 4.527 |
| 2026-10-04T12:27:46.231773+00:00 | 1.0000 | 4.6249 | 5.8875 | no samples | 4.727 |

Selected HTTP and endpoint observations:

```json
[
  {
    "timestamp": "2026-10-04T12:25:59.506641+00:00",
    "http": [
      {
        "timestamp": "2026-10-04T12:25:51.181613+00:00",
        "path": "/events",
        "exit_code": 0,
        "response_summary": "HTTP_STATUS=200 TOTAL_SECONDS=0.009199",
        "error_body": null
      },
      {
        "timestamp": "2026-10-04T12:25:56.455521+00:00",
        "path": "/events/1/reserve",
        "exit_code": 0,
        "response_summary": "HTTP_STATUS=504 TOTAL_SECONDS=5.018452",
        "error_body": "{\"detail\":\"Events service timeout\"}"
      },
      {
        "timestamp": "2026-10-04T12:25:59.318716+00:00",
        "path": "/health",
        "exit_code": 0,
        "response_summary": "HTTP_STATUS=503 TOTAL_SECONDS=2.027189",
        "error_body": "{\"status\":\"degraded\",\"checks\":{\"events\":\"down\",\"payments\":\"ok\",\"circuit_payments\":\"CLOSED\"}}"
      }
    ],
    "events_endpoints": [
      {
        "addresses": [
          "10.42.0.51"
        ],
        "conditions": {
          "ready": true,
          "serving": true,
          "terminating": false
        },
        "nodeName": "k3d-quickticket-server-0",
        "targetRef": {
          "kind": "Pod",
          "name": "events-8c7ff5ddb-j4l8x",
          "namespace": "default",
          "uid": "9558b26d-e1a8-4d1c-ae96-13bd39bc49c3"
        }
      }
    ]
  },
  {
    "timestamp": "2026-10-04T12:26:18.964104+00:00",
    "http": [
      {
        "timestamp": "2026-10-04T12:26:16.175189+00:00",
        "path": "/events",
        "exit_code": 0,
        "response_summary": "HTTP_STATUS=502 TOTAL_SECONDS=0.017177",
        "error_body": "{\"detail\":\"Events service unavailable\"}"
      },
      {
        "timestamp": "2026-10-04T12:26:16.390055+00:00",
        "path": "/events/1/reserve",
        "exit_code": 0,
        "response_summary": "HTTP_STATUS=502 TOTAL_SECONDS=0.090853",
        "error_body": "{\"detail\":\"Events service unavailable\"}"
      },
      {
        "timestamp": "2026-10-04T12:26:18.781201+00:00",
        "path": "/health",
        "exit_code": 0,
        "response_summary": "HTTP_STATUS=503 TOTAL_SECONDS=2.131221",
        "error_body": "{\"status\":\"degraded\",\"checks\":{\"events\":\"down\",\"payments\":\"ok\",\"circuit_payments\":\"CLOSED\"}}"
      }
    ],
    "events_endpoints": [
      {
        "addresses": [
          "10.42.0.51"
        ],
        "conditions": {
          "ready": false,
          "serving": false,
          "terminating": false
        },
        "nodeName": "k3d-quickticket-server-0",
        "targetRef": {
          "kind": "Pod",
          "name": "events-8c7ff5ddb-j4l8x",
          "namespace": "default",
          "uid": "9558b26d-e1a8-4d1c-ae96-13bd39bc49c3"
        }
      }
    ]
  },
  {
    "timestamp": "2026-10-04T12:27:46.231773+00:00",
    "http": [
      {
        "timestamp": "2026-10-04T12:27:45.803282+00:00",
        "path": "/events",
        "exit_code": 0,
        "response_summary": "HTTP_STATUS=502 TOTAL_SECONDS=0.006175",
        "error_body": "{\"detail\":\"Events service unavailable\"}"
      },
      {
        "timestamp": "2026-10-04T12:27:45.907992+00:00",
        "path": "/events/1/reserve",
        "exit_code": 0,
        "response_summary": "HTTP_STATUS=502 TOTAL_SECONDS=0.003599",
        "error_body": "{\"detail\":\"Events service unavailable\"}"
      },
      {
        "timestamp": "2026-10-04T12:27:46.008976+00:00",
        "path": "/health",
        "exit_code": 0,
        "response_summary": "HTTP_STATUS=503 TOTAL_SECONDS=0.016127",
        "error_body": "{\"status\":\"degraded\",\"checks\":{\"events\":\"down\",\"payments\":\"ok\",\"circuit_payments\":\"CLOSED\"}}"
      }
    ],
    "events_endpoints": [
      {
        "addresses": [
          "10.42.0.51"
        ],
        "conditions": {
          "ready": false,
          "serving": false,
          "terminating": false
        },
        "nodeName": "k3d-quickticket-server-0",
        "targetRef": {
          "kind": "Pod",
          "name": "events-8c7ff5ddb-j4l8x",
          "namespace": "default",
          "uid": "9558b26d-e1a8-4d1c-ae96-13bd39bc49c3"
        }
      }
    ]
  }
]
```

**Comparison:** event listing initially returned 200, while the first observed
reservation returned 504 after approximately five seconds. Events subsequently
lost Ready status; listing and reservation then returned 502 through the gateway.
Gateway health returned 503 and reported Events down. The sampled gateway
5xx ratio eventually reached 1.0.

The broader read outage matched the hypothesis about dependency-sensitive probes.
Both Events readiness and liveness used `/health`, which included Redis health.
Redis failure therefore affected Service eligibility and could trigger restarts,
expanding the impact beyond reservation holds.

After restoring Redis, gateway health recovered at the recorded timestamp.
The one-minute error window cleared later. A separate manual Events restart
was not needed by the recovery script.

**To improve resilience against this failure, I would** separate Events process
liveness from dependency health and design readiness to preserve supported read
operations while rejecting Redis-dependent operations explicitly.

```json
[
  {
    "timestamp": "2026-10-04T12:29:49.844154+00:00",
    "path": "/events",
    "exit_code": 0,
    "response_summary": "HTTP_STATUS=200 TOTAL_SECONDS=0.006826",
    "error_body": null
  },
  {
    "timestamp": "2026-10-04T12:29:49.931868+00:00",
    "path": "/events/1/reserve",
    "exit_code": 0,
    "response_summary": "HTTP_STATUS=200 TOTAL_SECONDS=0.006702",
    "error_body": null
  },
  {
    "timestamp": "2026-10-04T12:29:50.020675+00:00",
    "path": "/health",
    "exit_code": 0,
    "response_summary": "HTTP_STATUS=200 TOTAL_SECONDS=0.006918",
    "error_body": "{\"status\":\"healthy\",\"checks\":{\"events\":\"ok\",\"payments\":\"ok\",\"circuit_payments\":\"CLOSED\"}}"
  }
]
```

```text
NAME        READY   UP-TO-DATE   AVAILABLE   AGE
events      1/1     1            1           6d20h
redis       1/1     1            1           6d20h
mixedload   2/2     2            2           19m
```

## Task 2 — Combined failure scenario

```json
{
  "written_at": "2026-10-04T12:31:46.989720+00:00",
  "scenario": "Degraded payments plus constrained Events DB pool under increased load",
  "changes": {
    "PAYMENT_FAILURE_RATE": "0.3",
    "PAYMENT_LATENCY_MS": "500",
    "DB_MAX_CONNS": "3",
    "mixedload_replicas": 3
  },
  "reason": "Test whether payment failures and latency remain isolated to checkout or combine with Events connection pressure to disrupt reads and reservations.",
  "hypothesis": "Payment-path latency and 5xx should rise. Sequential checkout load may reduce achieved throughput despite three load replicas. A three-connection pool could produce pool-exhaustion errors under concurrent requests; whether it actually does so must be established from metrics and logs.",
  "observation_seconds": 200
}
```

```bash
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.3 PAYMENT_LATENCY_MS=500
kubectl set env deployment/events DB_MAX_CONNS=3
kubectl scale deployment/mixedload --replicas=3
kubectl rollout status deployment/payments
kubectl rollout status deployment/events
kubectl rollout status deployment/mixedload
```

```json
{
  "design_written_at": "2026-10-04T12:31:46.989720+00:00",
  "injection_started_at": "2026-10-04T12:31:46.990613+00:00",
  "all_injected_deployments_ready_at": "2026-10-04T12:31:56.050496+00:00",
  "observation_finished_at": "2026-10-04T12:35:25.349114+00:00",
  "restore_started_at": "2026-10-04T12:35:25.860124+00:00",
  "restored_deployments_ready_at": "2026-10-04T12:35:36.232354+00:00"
}
```

| UTC timestamp | 5xx ratio | Events p99 (s) | Reserve p99 (s) | Pay p99 (s) | Total RPS |
|---|---:|---:|---:|---:|---:|
| 2026-10-04T12:31:56.051636+00:00 | 0.0000 | 0.0864 | 0.1727 | 0.0934 | 16.764 |
| 2026-10-04T12:32:12.391971+00:00 | 0.0133 | 0.0933 | 0.1973 | 0.7347 | 15.219 |
| 2026-10-04T12:32:28.094116+00:00 | 0.0400 | 0.0806 | 0.1862 | 0.7438 | 13.842 |
| 2026-10-04T12:32:43.851273+00:00 | 0.0652 | 0.0844 | 0.1660 | 0.7464 | 12.134 |
| 2026-10-04T12:32:59.636267+00:00 | 0.0916 | 0.0239 | 0.0256 | 0.7475 | 10.646 |
| 2026-10-04T12:33:15.639562+00:00 | 0.0922 | 0.0245 | 0.0232 | 0.7475 | 10.655 |
| 2026-10-04T12:33:31.657751+00:00 | 0.0867 | 0.0246 | 0.0209 | 0.7475 | 10.493 |
| 2026-10-04T12:33:47.640165+00:00 | 0.0856 | 0.0246 | 0.0221 | 0.7475 | 10.618 |
| 2026-10-04T12:34:03.647294+00:00 | 0.0889 | 0.0260 | 0.0223 | 0.7475 | 10.636 |
| 2026-10-04T12:34:19.523578+00:00 | 0.0934 | 0.0246 | 0.0218 | 0.7475 | 10.564 |
| 2026-10-04T12:34:36.448166+00:00 | 0.1017 | 0.0245 | 0.0216 | 0.7475 | 10.546 |
| 2026-10-04T12:34:52.260587+00:00 | 0.1052 | 0.0245 | 0.0204 | 0.7475 | 10.546 |
| 2026-10-04T12:35:07.942223+00:00 | 0.1105 | 0.0241 | 0.0107 | 0.7475 | 10.527 |
| 2026-10-04T12:35:23.725377+00:00 | 0.1156 | 0.0245 | 0.0100 | 0.7475 | 10.691 |

**Golden signals:** in the first changed sample, at `12:32:12Z`, both error
ratio and payment p99 had increased, while throughput decreased.
The sampling interval cannot establish which reacted first within that interval.
The first sample after rollout still contained baseline traffic in the rate window.

**Worst latency amplification:** `/reserve/{id}/pay` rose from a baseline near
0.07 seconds to approximately 0.7475 seconds. At steady state, event reads
and reservations remained much faster. Overall 5xx ratio reached approximately
11.56% in the recorded combined observation window. This is the gateway-wide
ratio, not the injected 30% failure probability for charge requests.

**Weakest link under this tested workload:** payments. Its injected delay and
failures affected checkout directly, and the sequential workload reduced achieved
throughput. The saved Events log excerpt contained no pool-exhaustion errors;
the experiment does not establish the three-connection DB pool as a bottleneck.

I would improve payment resilience with dependency-specific latency/error alerts,
bounded requests, and carefully designed idempotent recovery for failed payments.
Retries must account for whether a charge succeeded before a response was lost.

The scenario remained active for more than three minutes after the changed
deployments became ready. Normal payment configuration, DB_MAX_CONNS=10, and
two mixedload replicas were restored before the bonus.

Selected injected payment failures:

```text
2026-10-04T12:35:13.438690087Z {"time":"2026-10-04 12:35:13,437","level":"WARNING","service":"payments","msg":"Payment failed (injected) for 0a0fba53-7ec7-47b5-b1ae-2e7ebc90db4b"}
2026-10-04T12:35:14.583769046Z {"time":"2026-10-04 12:35:14,583","level":"WARNING","service":"payments","msg":"Payment failed (injected) for 4cce2110-2098-44a9-bd13-4092fa44a1f7"}
2026-10-04T12:35:15.678780671Z {"time":"2026-10-04 12:35:15,678","level":"WARNING","service":"payments","msg":"Payment failed (injected) for fcdb9f84-70ff-4c8a-a01d-d85c02072784"}
2026-10-04T12:35:16.513495630Z {"time":"2026-10-04 12:35:16,513","level":"WARNING","service":"payments","msg":"Payment failed (injected) for 4dcebd12-0091-4c6c-9746-65934972b417"}
2026-10-04T12:35:16.822956922Z {"time":"2026-10-04 12:35:16,822","level":"WARNING","service":"payments","msg":"Payment failed (injected) for c387382a-9515-4fb2-8a6c-f8a155228930"}
```

```json
[
  {
    "timestamp": "2026-10-04T12:35:25.486116+00:00",
    "path": "/events",
    "exit_code": 0,
    "response_summary": "HTTP_STATUS=200 TOTAL_SECONDS=0.010485",
    "error_body": null
  },
  {
    "timestamp": "2026-10-04T12:35:25.578442+00:00",
    "path": "/events/1/reserve",
    "exit_code": 0,
    "response_summary": "HTTP_STATUS=200 TOTAL_SECONDS=0.008330",
    "error_body": null
  },
  {
    "timestamp": "2026-10-04T12:35:25.669344+00:00",
    "path": "/health",
    "exit_code": 0,
    "response_summary": "HTTP_STATUS=200 TOTAL_SECONDS=0.009471",
    "error_body": "{\"status\":\"healthy\",\"checks\":{\"events\":\"ok\",\"payments\":\"ok\",\"circuit_payments\":\"CLOSED\"}}"
  }
]
```

```json
{
  "timestamp": "2026-10-04T12:37:14.792271+00:00",
  "rps_by_path": [
    {
      "metric": {
        "path": "/events"
      },
      "value": [
        1791117435.277,
        "5.5817676071825435"
      ]
    },
    {
      "metric": {
        "path": "/health"
      },
      "value": [
        1791117435.277,
        "0"
      ]
    },
    {
      "metric": {
        "path": "/events/{id}/reserve"
      },
      "value": [
        1791117435.277,
        "5.6726843014515245"
      ]
    },
    {
      "metric": {
        "path": "/reserve/{id}/pay"
      },
      "value": [
        1791117435.277,
        "5.509052235113435"
      ]
    }
  ],
  "status_rps": [
    {
      "metric": {
        "status": "200"
      },
      "value": [
        1791117435.367,
        "16.763504143747504"
      ]
    },
    {
      "metric": {
        "status": "502"
      },
      "value": [
        1791117435.367,
        "0"
      ]
    },
    {
      "metric": {
        "status": "504"
      },
      "value": [
        1791117435.367,
        "0"
      ]
    },
    {
      "metric": {
        "status": "503"
      },
      "value": [
        1791117435.367,
        "0"
      ]
    },
    {
      "metric": {
        "status": "500"
      },
      "value": [
        1791117435.367,
        "0"
      ]
    }
  ],
  "error_ratio": [
    {
      "metric": {},
      "value": [
        1791117435.45,
        "0"
      ]
    }
  ],
  "p99_by_path": [
    {
      "metric": {
        "path": "/health"
      },
      "value": [
        1791117435.53,
        "NaN"
      ]
    },
    {
      "metric": {
        "path": "/events/{id}/reserve"
      },
      "value": [
        1791117435.53,
        "0.09024994090878091"
      ]
    },
    {
      "metric": {
        "path": "/reserve/{id}/pay"
      },
      "value": [
        1791117435.53,
        "0.06865633174639425"
      ]
    },
    {
      "metric": {
        "path": "/events"
      },
      "value": [
        1791117435.53,
        "0.05775063939281935"
      ]
    }
  ],
  "pay_observations_1m": [
    {
      "metric": {},
      "value": [
        1791117435.611,
        "330.54313410680606"
      ]
    }
  ]
}
```

## Bonus — Latency detection improvement

```json
{
  "written_at": "2026-10-04T12:41:28.628728+00:00",
  "weakness": "Slow successful payments had no Prometheus alert during Experiment 2",
  "fix": "Add payment-path p99 > 1s alert, for 1m, with request-rate guard > 0.1/s",
  "repeat": "Same 2000ms payment latency, 5000ms gateway timeout, two mixedload replicas",
  "expected": "Latency alert fires despite near-zero gateway 5xx, then resolves after recovery",
  "tradeoff": "Detection improves; latency itself is unchanged. Threshold and traffic guard can miss sparse incidents.",
  "notification_scope": "Prometheus rule state only; no Alertmanager or external notification configured"
}
```

### Implemented configuration change

`monitoring/lab8/prometheus.yaml` extends the supplied Lab 7 Prometheus
manifest with a rule file in the existing ConfigMap and a `rule_files`
entry. It can be applied to the existing monitoring stack.

Apply with `kubectl apply -f monitoring/lab8/prometheus.yaml`, then restart
the Prometheus Deployment so it loads the configuration. Its ephemeral
history was reset during this exercise; baseline samples were collected again
before repeating the same 2000 ms payment-latency injection.

```diff
diff --git a/labs/lab7/prometheus.yaml b/monitoring/lab8/prometheus.yaml
index 514eb40..1c1ac15 100644
--- a/labs/lab7/prometheus.yaml
+++ b/monitoring/lab8/prometheus.yaml
@@ -50,6 +50,8 @@ data:
     global:
       scrape_interval: 5s
       evaluation_interval: 5s
+    rule_files:
+      - /etc/prometheus/lab8-alerts.yml
     scrape_configs:
       - job_name: gateway
         kubernetes_sd_configs:
@@ -73,6 +75,32 @@ data:
           # into rs_hash so the AnalysisTemplate query can filter canary vs stable.
           - source_labels: [__meta_kubernetes_pod_label_rollouts_pod_template_hash]
             target_label: rs_hash
+  lab8-alerts.yml: |
+    groups:
+      - name: quickticket-latency
+        interval: 5s
+        rules:
+          - alert: QuickTicketPaymentHighLatency
+            expr: |
+              (
+                histogram_quantile(
+                  0.99,
+                  sum by (le) (
+                    rate(gateway_request_duration_seconds_bucket{path="/reserve/{id}/pay"}[1m])
+                  )
+                ) > 1
+              )
+              and
+              (
+                sum(rate(gateway_request_duration_seconds_count{path="/reserve/{id}/pay"}[1m])) > 0.1
+              )
+            for: 1m
+            labels:
+              severity: warning
+              service: gateway
+            annotations:
+              summary: "Payment-path p99 exceeds 1 second"
+              description: "Check payment latency and checkout throughput even when HTTP responses succeed."
 ---
 apiVersion: apps/v1
 kind: Deployment
```

```text
Checking /etc/prometheus/lab8-alerts.yml
  SUCCESS: 1 rules found
```

### Before versus after

| Check | Before improvement | After improvement |
|---|---|---|
| Fault | Payments latency 2000 ms | Same latency, same two-worker workload |
| Established payment p99 | Approximately 2.485 s | Approximately 2.485 s |
| Alert coverage | Prometheus rule groups empty | Payment latency alert installed |
| Detection | No latency alert | Pending, then Firing |
| 5xx at selected Firing sample | Original samples zero | Zero |
| Recovery | Healthy after restoring latency | Healthy, alert later inactive |

```json
{
  "status": "success",
  "data": {
    "groups": []
  }
}
```

```json
{
  "status": "success",
  "data": {
    "groups": [
      {
        "name": "quickticket-latency",
        "file": "/etc/prometheus/lab8-alerts.yml",
        "rules": [
          {
            "state": "firing",
            "name": "QuickTicketPaymentHighLatency",
            "query": "(histogram_quantile(0.99, sum by (le) (rate(gateway_request_duration_seconds_bucket{path=\"/reserve/{id}/pay\"}[1m]))) > 1) and (sum(rate(gateway_request_duration_seconds_count{path=\"/reserve/{id}/pay\"}[1m])) > 0.1)",
            "duration": 60,
            "keepFiringFor": 0,
            "labels": {
              "service": "gateway",
              "severity": "warning"
            },
            "annotations": {
              "description": "Check payment latency and checkout throughput even when HTTP responses succeed.",
              "summary": "Payment-path p99 exceeds 1 second"
            },
            "alerts": [
              {
                "labels": {
                  "alertname": "QuickTicketPaymentHighLatency",
                  "service": "gateway",
                  "severity": "warning"
                },
                "annotations": {
                  "description": "Check payment latency and checkout throughput even when HTTP responses succeed.",
                  "summary": "Payment-path p99 exceeds 1 second"
                },
                "state": "firing",
                "activeAt": "2026-10-04T12:41:46.627560481Z",
                "value": "2.485e+00"
              }
            ],
            "health": "ok",
            "evaluationTime": 0.002056291,
            "lastEvaluation": "2026-10-04T12:42:56.629586051Z",
            "type": "alerting"
          }
        ],
        "interval": 5,
        "limit": 0,
        "evaluationTime": 0.002159917,
        "lastEvaluation": "2026-10-04T12:42:56.629511426Z"
      }
    ]
  }
}
```

```json
{
  "status": "success",
  "data": {
    "groups": [
      {
        "name": "quickticket-latency",
        "file": "/etc/prometheus/lab8-alerts.yml",
        "rules": [
          {
            "state": "inactive",
            "name": "QuickTicketPaymentHighLatency",
            "query": "(histogram_quantile(0.99, sum by (le) (rate(gateway_request_duration_seconds_bucket{path=\"/reserve/{id}/pay\"}[1m]))) > 1) and (sum(rate(gateway_request_duration_seconds_count{path=\"/reserve/{id}/pay\"}[1m])) > 0.1)",
            "duration": 60,
            "keepFiringFor": 0,
            "labels": {
              "service": "gateway",
              "severity": "warning"
            },
            "annotations": {
              "description": "Check payment latency and checkout throughput even when HTTP responses succeed.",
              "summary": "Payment-path p99 exceeds 1 second"
            },
            "alerts": [],
            "health": "ok",
            "evaluationTime": 0.002134334,
            "lastEvaluation": "2026-10-04T12:45:16.628623921Z",
            "type": "alerting"
          }
        ],
        "interval": 5,
        "limit": 0,
        "evaluationTime": 0.002423333,
        "lastEvaluation": "2026-10-04T12:45:16.628359421Z"
      }
    ]
  }
}
```

```json
{
  "design_written_at": "2026-10-04T12:41:28.628728+00:00",
  "injection_started_at": "2026-10-04T12:41:28.629169+00:00",
  "injected_deployment_ready_at": "2026-10-04T12:41:37.591687+00:00",
  "first_pending_at": "2026-10-04T12:41:54.319590+00:00",
  "first_firing_at": "2026-10-04T12:42:59.304060+00:00",
  "restore_started_at": "2026-10-04T12:43:48.409167+00:00",
  "restored_deployment_ready_at": "2026-10-04T12:43:56.798243+00:00",
  "inactive_observed_at": "2026-10-04T12:45:18.323039+00:00"
}
```

The alert was first observed Firing **90.67
seconds** after injection. The API recorded its active condition from
`12:41:46.627560481Z`; the one-minute pending period and polling cadence explain
the observed delay.

Early repeat samples contained small transient 5xx ratios (up to approximately
0.823%), so the repeat was not uniformly error-free. At the saved Firing
sample, the 5xx ratio was zero while payment p99 was 2.485 seconds:
latency detection provided information an error-only alert would miss.

The alert resolved after payment latency was restored and the rolling histogram
window cleared. `/health` was healthy after recovery.

**Tradeoff:** this improves detection, not payment performance. Histogram
estimates, the one-minute pending period, and the traffic guard can delay or miss
sparse incidents. This is Prometheus alert-state evidence; Alertmanager delivery
and external notifications were not configured or claimed.

## Cleanup and final state

```text
UPDATE 1
 id | total_tickets
----+---------------
  1 |           100
(1 row)

 retained_event1_orders
------------------------
                   9717
(1 row)
```

```json
{"status":"healthy","checks":{"events":"ok","payments":"ok","circuit_payments":"CLOSED"}}
```

```json
{
  "source": {
    "path": "k8s",
    "repoURL": "https://github.com/Esqavator/SRE-Intro.git",
    "targetRevision": "feature/lab7"
  },
  "syncPolicy": {
    "automated": {
      "prune": true,
      "selfHeal": true
    }
  },
  "sync": {
    "comparedTo": {
      "destination": {
        "namespace": "default",
        "server": "https://kubernetes.default.svc"
      },
      "source": {
        "path": "k8s",
        "repoURL": "https://github.com/Esqavator/SRE-Intro.git",
        "targetRevision": "feature/lab7"
      }
    },
    "revision": "63f6bae7f76c36c6fbcd1787e4beed9dd36e0703",
    "status": "Synced"
  },
  "health": {
    "lastTransitionTime": "2026-10-04T12:43:56Z",
    "status": "Healthy"
  }
}
```

Mixedload was removed, Redis returned to one replica, payment fault settings
returned to zero, and Events DB_MAX_CONNS returned to 10. ArgoCD again manages
the Lab 7 application with auto-sync and self-heal. The gateway retains five
ready replicas. The latency alert remains installed in monitoring.

Event 1's configured capacity was restored to 100, but 9717 orders were retained
after the local load exercise. Its available inventory is therefore exhausted;
healthy infrastructure does not imply available tickets. Future checkout tests
must prepare a test inventory or use another event with available tickets.

The submission contains this report and the reproducible monitoring configuration.
No prior lab report or application manifest is added to the Lab 8 PR.
