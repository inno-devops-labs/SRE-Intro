import ast
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]


def documents(path):
    return list(yaml.safe_load_all((ROOT / path).read_text()))


def test_service_replica_manifests():
    for service in ("events", "payments", "notifications"):
        deployment, svc = documents(f"k8s/{service}.yaml")
        assert deployment["kind"] == "Deployment"
        assert deployment["spec"]["replicas"] == 2
        assert deployment["spec"]["selector"]["matchLabels"] == {"app": service}
        assert svc["kind"] == "Service"
        assert svc["spec"]["selector"] == {"app": service}

    events = documents("k8s/events.yaml")[0]["spec"]["template"]["spec"]
    assert events["terminationGracePeriodSeconds"] == 40
    container = events["containers"][0]
    assert container["lifecycle"]["preStop"]["exec"]["command"] == [
        "sh", "-c", "sleep 10"
    ]
    assert container["readinessProbe"]["httpGet"] == {
        "path": "/health", "port": 8081
    }


def test_pdb_contracts():
    pdbs = {doc["metadata"]["name"]: doc for doc in documents("k8s/pdb.yaml")}
    assert pdbs["gateway-pdb"]["spec"]["minAvailable"] == 2
    assert pdbs["events-pdb"]["spec"]["minAvailable"] == 1
    assert pdbs["payments-pdb"]["spec"]["minAvailable"] == 1
    assert pdbs["notifications-pdb"]["spec"]["maxUnavailable"] == 1


def test_gateway_shutdown_spread_and_hpa():
    rollout = documents("k8s/gateway.yaml")[0]
    pod_spec = rollout["spec"]["template"]["spec"]
    assert pod_spec["terminationGracePeriodSeconds"] == 40
    assert pod_spec["topologySpreadConstraints"][0]["maxSkew"] == 1
    container = pod_spec["containers"][0]
    assert container["lifecycle"]["preStop"]["exec"]["command"] == [
        "sh", "-c", "sleep 10"
    ]
    assert container["readinessProbe"]["httpGet"]["path"] == "/health"

    hpa = documents("k8s/gateway-hpa.yaml")[0]
    assert hpa["spec"]["minReplicas"] == 5
    assert hpa["spec"]["maxReplicas"] == 12
    assert hpa["spec"]["metrics"][0]["resource"]["target"]["averageUtilization"] == 70


def test_migration_chain_and_final_schema_references():
    versions = sorted((ROOT / "migrations/versions").glob("*.py"))
    assert len(versions) == 6
    for version in versions:
        ast.parse(version.read_text())

    index = (ROOT / "migrations/versions/1201_index_events_event_date_concurrently.py").read_text()
    assert "autocommit_block" in index
    assert "postgresql_concurrently=True" in index
    assert "if_not_exists=True" in index

    assert 'down_revision = "348211cb15a0"' in index
    expand = (ROOT / "migrations/versions/1202_add_events_scheduled_at.py").read_text()
    assert "if_not_exists=True" in expand

    source = (ROOT / "app/events/main.py").read_text()
    seed = (ROOT / "app/seed.sql").read_text()
    assert "e.scheduled_at" in source
    assert "e.event_date" not in source
    assert "scheduled_at TIMESTAMPTZ NOT NULL" in seed
    assert "event_date TIMESTAMPTZ" not in seed
