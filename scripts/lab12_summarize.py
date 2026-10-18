"""Extract numeric Prometheus evidence without comparing JSON timestamps."""
import json
from pathlib import Path

folder = Path(__file__).resolve().parents[1] / 'labs/lab12/evidence'
phases = ['before-kill', 'after-kill', 'after-restart', 'after-index',
          'bonus-baseline', 'after-m1', 'after-deploy-a', 'after-m2',
          'after-deploy-b', 'bonus-final']
keys = ['5xx_total', '5xx_increase_3m', 'requests_increase_3m',
        'scrape_targets', 'checkout_success_3m']
rows = []
for phase in phases:
    lines = (folder / f'snapshot-{phase}.log').read_text().splitlines()
    row = {'phase': phase, 'utc': lines[0]}
    for index, line in enumerate(lines):
        if line in keys:
            result = json.loads(lines[index + 2])
            assert result['status'] == 'success'
            row[line] = float(result['data']['result'][0]['value'][1])
    assert all(key in row for key in keys), row
    assert row['5xx_total'] == row['5xx_increase_3m'] == 0, row
    assert row['scrape_targets'] == 5, row
    assert row['requests_increase_3m'] > 0 and row['checkout_success_3m'] > 0, row
    rows.append(row)
(folder / 'metrics-summary.json').write_text(json.dumps(rows, indent=2) + '\n')
for phase, name in [('bonus-baseline', '5xx-baseline.txt'), ('bonus-final', '5xx-final.txt')]:
    value = next(row['5xx_total'] for row in rows if row['phase'] == phase)
    (folder / name).write_text(f'{value:g}\n')
print('PASS all checkpoints: 0 observed 5xx, 5 scrape targets, positive traffic and checkout success')
print('Bonus baseline =', (folder / '5xx-baseline.txt').read_text().strip())
print('Bonus final =', (folder / '5xx-final.txt').read_text().strip())
