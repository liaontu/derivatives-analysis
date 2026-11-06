"""Small, separately published collection receipts; never advance a market date."""
from datetime import datetime, timezone
from pathlib import Path
import argparse
import json
import os


def record(output, kind, outcome, run_id):
    if kind not in ('market', 'branches') or outcome not in ('success', 'failure') or not run_id.isdecimal():
        raise ValueError('Invalid collector receipt')
    output.mkdir(exist_ok=True, parents=True)
    payload = {'checkedAt': datetime.now(timezone.utc).isoformat(timespec='seconds'),
               'outcome': outcome, 'runId': run_id}
    (output / f'check-{kind}.json').write_text(json.dumps(payload, separators=(',', ':')), encoding='utf8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('kind', choices=['market', 'branches'])
    parser.add_argument('outcomes', nargs='+', choices=['success', 'failure'])
    args = parser.parse_args()
    record(Path('data/derivatives'), args.kind, 'failure' if 'failure' in args.outcomes else 'success', os.environ['GITHUB_RUN_ID'])
