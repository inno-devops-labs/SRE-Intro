"""Destructive DB exercises, restricted to the isolated course context."""
import datetime, json, pathlib, subprocess, time, urllib.request, urllib.parse, sys, os
E=pathlib.Path('submissions/evidence/lab9'); E.mkdir(parents=True,exist_ok=True)
log=(E/'session.txt').open('w')
def run(args, data=None, check=True):
    log.write('$ '+' '.join(args)+'\n'); log.flush()
    p=subprocess.run(args,input=data,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    out=p.stdout.decode(errors='replace'); log.write(out+'\n');log.flush();print(out,flush=True)
    if check and p.returncode: raise RuntimeError(args)
    return out

def sql(q): return run(['kubectl','exec','deploy/postgres','--','psql','-v','ON_ERROR_STOP=1','-U','quickticket','-d','quickticket','-Atc',q])
def counts(): return sql('SELECT count(*) FROM events; SELECT count(*) FROM orders;').strip().splitlines()
def stamp(): return datetime.datetime.now(datetime.timezone.utc).isoformat()
def prom(name):
    q='sum(increase(gateway_requests_total{status=~"5.."}[1m]))'
    d=json.load(urllib.request.urlopen('http://localhost:19090/api/v1/query?'+urllib.parse.urlencode({'query':q})))
    (E/(name+'.json')).write_text(json.dumps(d,indent=2));return d

def ready():
    run(['kubectl','rollout','status','deploy/postgres','--timeout=90s'])
def appready():
    run(['kubectl','rollout','restart','deploy/events'])
    run(['kubectl','rollout','status','deploy/events','--timeout=90s'])
    for _ in range(60):
        try:
            with urllib.request.urlopen('http://localhost:18080/events',timeout=5) as r:
                if r.status==200: return stamp()
        except Exception: time.sleep(1)
    raise RuntimeError('API did not recover')
assert run(['kubectl','config','current-context']).strip()=='k3d-quickticket-lab78'
run(['kubectl','exec','-i','deploy/postgres','--','psql','-U','quickticket','-d','quickticket'],pathlib.Path('scripts/lab8-seed.sql').read_bytes())
run(['kubectl','apply','-f','k8s/mixedload.yaml'])
run(['kubectl','rollout','status','deploy/mixedload','--timeout=90s'])
time.sleep(35)
alembic=os.environ.get('ALEMBIC_BIN', str(pathlib.Path(sys.executable).with_name('alembic')))
run([alembic,'stamp','0001']); before=prom('migration-before'); t=time.monotonic();run([alembic,'upgrade','head']); elapsed=time.monotonic()-t
run([alembic,'history']);run(['kubectl','exec','deploy/postgres','--','psql','-U','quickticket','-d','quickticket','-c','\\d events'])
time.sleep(35); after=prom('migration-after')
dump_time=stamp();dump_start=time.monotonic()
with open('/tmp/quickticket.dump','wb') as f: subprocess.run(['kubectl','exec','deploy/postgres','--','pg_dump','-U','quickticket','-Fc','quickticket'],stdout=f,check=True)
run(['ls','-lh','/tmp/quickticket.dump']);run(['file','/tmp/quickticket.dump'])
pod=run(['kubectl','get','pod','-l','app=postgres','-o','jsonpath={.items[0].metadata.name}']).strip();run(['kubectl','cp','/tmp/quickticket.dump',pod+':/tmp/backup.dump'])
run(['kubectl','exec','deploy/postgres','--','pg_restore','--list','/tmp/backup.dump'])
backup_counts=counts();run(['kubectl','scale','deploy/mixedload','--replicas=0']);time.sleep(2)
before_drop=counts();sql('DROP TABLE orders CASCADE');after_drop=sql("SELECT count(*) FROM events; SELECT to_regclass('public.orders');")
run(['curl','-s','-o','/dev/null','-w','events after DROP: %{http_code}\n','http://localhost:18080/events'])
run(['kubectl','exec','deploy/postgres','--','pg_restore','--exit-on-error','-U','quickticket','-d','quickticket','--clean','--if-exists','/tmp/backup.dump'])
restored=counts();appready();run(['kubectl','scale','deploy/mixedload','--replicas=2']);time.sleep(10)
prekill=counts();kill=stamp();start=time.monotonic();run(['kubectl','delete','pod','-l','app=postgres','--wait=true']);ready();podready=stamp();empty=sql("SELECT to_regclass('public.events'), to_regclass('public.orders')")
pod=run(['kubectl','get','pod','-l','app=postgres','-o','jsonpath={.items[0].metadata.name}']).strip();run(['kubectl','cp','/tmp/quickticket.dump',pod+':/tmp/backup.dump'])
run(['kubectl','exec','deploy/postgres','--','pg_restore','--exit-on-error','-U','quickticket','-d','quickticket','--clean','--if-exists','/tmp/backup.dump']);restore_time=stamp();app_time=appready();rto=time.monotonic()-start
run(['kubectl','scale','deploy/mixedload','--replicas=0']);time.sleep(2);postkill=counts();prom('disaster-after')
result=dict(migration_seconds=elapsed,backup_time=dump_time,backup_bytes=pathlib.Path('/tmp/quickticket.dump').stat().st_size,backup_counts=backup_counts,before_drop=before_drop,after_drop=after_drop,restored=restored,prekill=prekill,postkill=postkill,kill=kill,podready=podready,empty=empty,restored_at=restore_time,appready=app_time,rto_seconds=rto,rpo_age_seconds=time.monotonic()-dump_start)
(E/'results.json').write_text(json.dumps(result,indent=2))
# Move the ephemeral DB to a fresh PVC, then restore the same verified backup.
run(['kubectl','apply','-f','k8s/postgres.yaml']);ready()
pod=run(['kubectl','get','pod','-l','app=postgres','-o','jsonpath={.items[0].metadata.name}']).strip();run(['kubectl','cp','/tmp/quickticket.dump',pod+':/tmp/backup.dump'])
run(['kubectl','exec','deploy/postgres','--','pg_restore','--exit-on-error','-U','quickticket','-d','quickticket','--clean','--if-exists','/tmp/backup.dump']);appready()
pvc_before=counts();kill2=stamp();start=time.monotonic();run(['kubectl','delete','pod','-l','app=postgres','--wait=true']);ready();pvc_after=counts();app2=appready()
result.update(pvc_before=pvc_before,pvc_after=pvc_after,pvc_kill=kill2,pvc_appready=app2,pvc_rto_seconds=time.monotonic()-start)
(E/'results.json').write_text(json.dumps(result,indent=2))
run(['kubectl','apply','-f','labs/lab9/backup-storage.yaml']);run(['kubectl','rollout','status','deploy/backup-inspector','--timeout=90s']);run(['kubectl','apply','-f','k8s/backup-cronjob.yaml'])
for i in range(1,8):
    name=f'lab9-manual-{i}';run(['kubectl','create','job','--from=cronjob/postgres-backup',name]);run(['kubectl','wait','--for=condition=Complete','job/'+name,'--timeout=90s']);run(['kubectl','logs','job/'+name])
run(['kubectl','exec','deploy/backup-inspector','--','ls','-la','/backups'])
run(['kubectl','get','pvc']); run(['kubectl','delete','deploy/mixedload'])
