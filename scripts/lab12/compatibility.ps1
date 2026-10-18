$ErrorActionPreference='Stop'
$pg=(kubectl get pod -l app=postgres -o json | ConvertFrom-Json).items[0].metadata.name
function Migrate($action,$revision) {
    kubectl exec lab12-client -- python /scripts/migrate.py $action $revision --database lab12_verify
    if ($LASTEXITCODE) { throw 'Temporary database migration failed' }
}
function Read-Compatibility($image,$database,$stage) {
    $template=(kubectl get deployment events -o json | ConvertFrom-Json).spec.template
    $template.spec.containers[0].image=$image
    ($template.spec.containers[0].env | Where-Object name -eq DB_NAME).value=$database
    $template.spec | Add-Member -Name restartPolicy -MemberType NoteProperty -Value Never -Force
    $pod=@{apiVersion='v1';kind='Pod';metadata=@{name='lab12-compat';labels=@{app='lab12-compat'}};spec=$template.spec}
    try {
        $pod | ConvertTo-Json -Depth 100 | kubectl create -f -
        if ($LASTEXITCODE) { throw 'Compatibility pod creation failed' }
        kubectl wait --for=condition=Ready pod/lab12-compat --timeout=90s
        if ($LASTEXITCODE) { throw 'Compatibility pod not Ready' }
        [DateTime]::UtcNow.ToString('o')
        Write-Output "COMPATIBILITY stage=$stage database=$database image=$image"
        kubectl exec lab12-compat -- python -c "import urllib.request,json; print(json.dumps([{ 'path':p,'status':(r:=urllib.request.urlopen('http://localhost:8081'+p)).status,'body':json.load(r)} for p in ['/health','/events','/events/3']]))"
        if ($LASTEXITCODE) { throw 'Compatibility read failed' }
    } finally {
        kubectl delete pod lab12-compat --ignore-not-found --wait=true
    }
}
Migrate upgrade 12a0001
Migrate downgrade 9a0002
Migrate upgrade 12a0001
Migrate upgrade 12a0002
Read-Compatibility 'quickticket-events:lab12-a-4f0dd1dfda5e' 'lab12_verify' 'A-on-M1'
Migrate upgrade 12a0003
Read-Compatibility 'quickticket-events:lab12-b-a0bc4751a840' 'lab12_verify' 'B-on-M2'
Migrate upgrade 12a0004
Migrate upgrade 12a0005
Read-Compatibility 'quickticket-events:lab12-b-a0bc4751a840' 'lab12_verify' 'B-after-contract'
Migrate downgrade 9a0001
Migrate upgrade 12a0005
kubectl exec lab12-client -- python /work/schema.py lab12_verify
kubectl exec $pg -- createdb -U quickticket lab12_fresh
if ($LASTEXITCODE) { throw 'Fresh test database already exists or cannot be created' }
Get-Content -Raw app/seed.sql | kubectl exec -i $pg -- psql -U quickticket -d lab12_fresh -v ON_ERROR_STOP=1
if ($LASTEXITCODE) { throw 'Fresh seed failed' }
kubectl cp scripts/lab12/bootstrap_check.py lab12-client:/work/bootstrap_check.py
kubectl exec lab12-client -- python /work/bootstrap_check.py
if ($LASTEXITCODE) { throw 'Full schema equality failed; do not stamp' }
Read-Compatibility 'quickticket-events:lab12-b-a0bc4751a840' 'lab12_fresh' 'B-fresh-seed'
kubectl exec lab12-client -- python /work/schema.py lab12_fresh
