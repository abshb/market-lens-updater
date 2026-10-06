"""Public orchestration only; datasets, model, and processing code stay private."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
from datetime import datetime, timezone

REPO = os.environ.get('STATE_REPO', 'abshb/market-lens-ios')
TAG = 'market-data-state'
PRIVATE = Path('private').resolve()


def gh(*args):
    return subprocess.check_output(['gh', *args], text=True)


def release():
    return json.loads(gh('api', f'repos/{REPO}/releases/tags/{TAG}'))


def restore():
    if not os.environ.get('GH_TOKEN'):
        raise RuntimeError('Configure the STATE_TOKEN repository secret first')
    PRIVATE.mkdir(exist_ok=True)
    assets = release()['assets']
    candidates = [a for a in assets if a['name'].startswith('state-') and a['name'].endswith('.tar.gz') and a['state'] == 'uploaded']
    if not candidates:
        raise RuntimeError('No complete private state archive found')
    newest = max(candidates, key=lambda a: (a['created_at'], a['id']))
    for name in ['backend-source.tar.gz', newest['name']]:
        gh('release', 'download', TAG, '--repo', REPO, '--pattern', name, '--dir', str(PRIVATE))
    with tarfile.open(PRIVATE / 'backend-source.tar.gz') as archive:
        members = archive.getmembers()
        for member in members:
            path = Path(member.name)
            if path.is_absolute() or '..' in path.parts or path.parts[0] != 'backend' or not (member.isfile() or member.isdir()):
                raise RuntimeError('Unsafe source archive')
        archive.extractall(PRIVATE, members=members, filter='data')
    sys.path.insert(0, str(PRIVATE / 'backend/scripts'))
    from cloud_refresh import restore_state
    restore_state(PRIVATE / newest['name'], PRIVATE / 'backend')
    print('Restored private processing code and saved data.')


def refresh():
    sys.path.insert(0, str(PRIVATE / 'backend/scripts'))
    from cloud_refresh import pack_state
    # Provider output stays off public Actions logs. Failed runs never replace state.
    with (PRIVATE / 'refresh.log').open('w') as log:
        result = subprocess.run([sys.executable, 'scripts/scheduled_refresh.py'], cwd=PRIVATE / 'backend', stdout=log, stderr=subprocess.STDOUT)
    if result.returncode:
        raise RuntimeError('Private refresh failed; previous saved data retained. No provider output published.')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    archive = PRIVATE / f'state-{stamp}.tar.gz'
    pack_state(archive, PRIVATE / 'backend')
    gh('release', 'upload', TAG, str(archive), '--repo', REPO)
    # Upload completes before pruning; always retain two complete generations.
    assets = release()['assets']
    states = sorted([a for a in assets if a['name'].startswith('state-') and a['name'].endswith('.tar.gz') and a['state'] == 'uploaded'], key=lambda a: (a['created_at'], a['id']), reverse=True)
    for old in states[2:]:
        gh('api', '--method', 'DELETE', f"repos/{REPO}/releases/assets/{old['id']}")
    print('Refresh succeeded; updated data saved privately.')


if __name__ == '__main__':
    {'restore': restore, 'refresh': refresh}[sys.argv[1]]()
