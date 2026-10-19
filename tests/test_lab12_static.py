"""Validate configuration consistency without contacting a Kubernetes cluster."""
import ast
from pathlib import Path
import yaml
import pytest

ROOT = Path(__file__).resolve().parents[1]


def manifests(name):
    return list(yaml.safe_load_all((ROOT / 'k8s' / name).read_text()))


def test_gateway_is_rollout():
    gateway = manifests('gateway.yaml')[0]
    assert gateway['kind'] == 'Rollout'
    assert gateway['spec']['replicas'] == 5


def test_gateway_spread_and_grace():
    spec = manifests('gateway.yaml')[0]['spec']['template']['spec']
    assert spec['terminationGracePeriodSeconds'] >= 40
    assert spec['topologySpreadConstraints'][0]['maxSkew'] == 1
    assert spec['topologySpreadConstraints'][0]['whenUnsatisfiable'] == 'ScheduleAnyway'


def test_gateway_prestop_and_readiness():
    container = manifests('gateway.yaml')[0]['spec']['template']['spec']['containers'][0]
    assert 'preStop' in container['lifecycle']
    assert container['readinessProbe']['httpGet']['path'] == '/health'
    assert container['readinessProbe']['failureThreshold'] == 1


@pytest.mark.parametrize('name', ['events', 'payments', 'notifications'])
def test_two_replicas(name):
    manifest = manifests(f'{name}.yaml')[0]
    assert manifest['kind'] == 'Deployment'
    assert manifest['spec']['replicas'] == 2


def test_budgets_match_spec():
    budgets = manifests('pdb.yaml')
    assert len(budgets) == 4
    budgets = {b['metadata']['name']: b['spec'] for b in budgets}
    assert budgets['gateway-pdb']['minAvailable'] == 2
    assert budgets['events-pdb']['minAvailable'] == 1
    assert budgets['payments-pdb']['minAvailable'] == 1
    assert budgets['notifications-pdb']['maxUnavailable'] == 1


def test_hpa_rollout_ref():
    spec = manifests('gateway-hpa.yaml')[0]['spec']
    assert spec['scaleTargetRef']['kind'] == 'Rollout'
    assert spec['minReplicas'] == 5
    assert spec['maxReplicas'] >= 10


def test_migrations_acyclic():
    files = sorted((ROOT / 'migrations' / 'versions').glob('*.py'))
    assert len(files) == 5
    revisions = {}
    for file in files:
        content = ast.parse(file.read_text())
        settings = {}
        for node in content.body:
            if isinstance(node, ast.Assign):
                if any(isinstance(target, ast.Name) and target.id in ('revision','down_revision') for target in node.targets):
                    settings[node.targets[0].id] = ast.literal_eval(node.value)
        revisions[settings['revision']] = settings['down_revision']
    roots = [key for key, parent in revisions.items() if parent is None]
    assert len(roots) == 1
    for revision, parent in revisions.items():
        if parent is not None:
            assert parent in revisions


def test_concurrent_index_uses_autocommit():
    source = (ROOT / 'migrations/versions/c12a00000002_event_date_index.py').read_text()
    assert 'autocommit_block()' in source
    assert 'postgresql_concurrently=True' in source


def test_backfill_before_drop():
    backfill = (ROOT / 'migrations/versions/c12a00000004_backfill_scheduled_at.py').read_text()
    drop = (ROOT / 'migrations/versions/c12a00000005_remove_event_date.py').read_text()
    assert 'WHERE scheduled_at IS NULL' in backfill
    assert 'nullable=False' in backfill
    assert 'drop_column("events", "event_date")' in drop


def test_event_code_uses_final_column():
    source = (ROOT / 'app/events/main.py').read_text()
    assert 'e.scheduled_at AS event_date' in source
    assert 'GROUP BY e.id ORDER BY e.scheduled_at' in source


def test_seed_new_schema():
    source = (ROOT / 'app/seed.sql').read_text()
    assert 'scheduled_at TIMESTAMPTZ NOT NULL' in source


def test_events_image_matches_manifest():
    spec = manifests('events.yaml')[0]['spec']['template']['spec']['containers'][0]
    assert spec['image'] == 'quickticket-events:lab12-b'
    assert spec['imagePullPolicy'] == 'IfNotPresent'
