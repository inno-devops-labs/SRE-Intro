global:
  scrape_interval: 15s
  evaluation_interval: 15s

rule_files:
  - "rules.yml"

scrape_configs:
  - job_name: gateway
    static_configs:
      - targets: ["gateway:8080"]
  - job_name: events
    static_configs:
      - targets: ["events:8081"]
  - job_name: payments
    static_configs:
      - targets: ["payments:8082"]
