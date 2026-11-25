"""Register only the included completed sample, safely after moving the folder."""
from pathlib import Path
import json
from nicegui_dashboard.team_update import TeamUpdates
from result_service.dashboard_contract.validation import validate_bundle

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data/results'
RUN = 'team-aa493b5f576c1019972a1aecfb1a'
NOMINAL = 'nominal-aa493b5f576c1019972a1aecfb1a'
REVISION = 'rev-f6d2a8c8c30c61e7eb90d2c4'

def main():
    manifests = {}
    for mode, run in [('v21', RUN), ('nominal', NOMINAL)]:
        manifests[mode] = validate_bundle(DATA / 'bundles' / run / REVISION)
    m = manifests['v21']
    flights = {mode: {'run_id': row['run_id'], 'revision': row['bundle_revision']}
               for mode, row in manifests.items()}
    updates = TeamUpdates(DATA, resume=False, max_output_gib=8)
    with updates.connection() as db:
        matches = db.execute("SELECT flights FROM jobs WHERE region=? AND state='completed'", ('bundang',)).fetchall()
        if not any(json.loads(row['flights'] or '{}') == flights for row in matches):
            bid = db.execute('INSERT INTO batches(sha,source,core,python,added,quota_gib) VALUES(?,?,?,?,?,?)',
                (m['team_commit'], 'included-record-only', 'included-record-only', 'included-record-only', m['completed_at'], 8)).lastrowid
            db.execute('INSERT INTO jobs(batch,region,state,phase,message,finished_at,flights) VALUES(?,?,?,?,?,?,?)',
                (bid, 'bundang', 'completed', 'published', '', m['completed_at'], json.dumps(flights)))
    assert any(row['flights'] == flights for row in updates.history('bundang'))
    print('Bundang recorded flights are ready. No simulation was started.')

if __name__ == '__main__':
    main()
