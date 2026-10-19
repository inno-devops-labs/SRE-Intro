"""Switch the Events SQL read expressions for migration Deploy A / Deploy B.

Usage: python3 scripts/lab12_stage.py A
       python3 scripts/lab12_stage.py B

The repository stores the final Deploy B version. This helper creates the
intermediate Deploy A code *only* after migration 1 has added scheduled_at.
"""
from pathlib import Path
import sys

TARGET = Path(__file__).resolve().parents[1] / "app/events/main.py"
OLD = "e.scheduled_at"
DUAL = "COALESCE(e.scheduled_at, e.event_date)"


def change(stage):
    if stage not in ("A", "B"):
        raise SystemExit("Usage: python3 scripts/lab12_stage.py A|B")
    source = TARGET.read_text(encoding="utf-8")
    if stage == "A":
        source = source.replace(DUAL, OLD)
        source = source.replace(OLD, DUAL)
    else:
        source = source.replace(DUAL, OLD)
    TARGET.write_text(source, encoding="utf-8")
    print("Events SQL configured for deploy", stage)


if __name__ == "__main__":
    change(sys.argv[1] if len(sys.argv) == 2 else "")
