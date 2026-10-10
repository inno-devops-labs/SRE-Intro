"""Update exactly one deployment image per service, without extra dependencies."""
import pathlib
import re
import sys

owner, sha = sys.argv[1:]
if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", owner):
    raise SystemExit("Expected a lowercase GitHub owner")
if not re.fullmatch(r"[0-9a-f]{40}", sha):
    raise SystemExit("Expected a full 40-character commit SHA")
updates = {}
for service in ("gateway", "events", "payments"):
    path = pathlib.Path("k8s") / f"{service}.yaml"
    content, count = re.subn(
        rf"(?m)^(\s*image: )\S*quickticket-{service}:\S+\s*$",
        rf"\g<1>ghcr.io/{owner}/quickticket-{service}:{sha}",
        path.read_text(),
    )
    if count != 1:
        raise SystemExit(f"{path}: expected one image, found {count}")
    updates[path] = content
for path, content in updates.items():
    path.write_text(content)
