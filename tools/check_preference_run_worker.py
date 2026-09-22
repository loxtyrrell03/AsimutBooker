"""Exercise the real hidden dispatcher with isolated fake Booker and settings."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import preference_runs as runs
from app_settings import load_settings
from runtime_guard import SingleInstanceLock


def wait_for(check, process, seconds=15):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return
        if process.poll() is not None:
            raise AssertionError('Dispatcher exited before the expected state')
        time.sleep(.05)
    raise AssertionError('Dispatcher did not reach the expected state')


def check():
    with tempfile.TemporaryDirectory(prefix='booker-preference-fixture-') as directory:
        root = Path(directory)
        for name in ('preference_runs.py', 'app_settings.py', 'runtime_guard.py'):
            shutil.copy2(ROOT / name, root / name)
        (root / 'gui.py').write_text('def query_recurring_task_status(timeout=10): return {"healthy": True}\n')
        (root / 'book_week.py').write_text('''import json,time
from pathlib import Path
ROOT=Path(__file__).resolve().parent
def main(flags):
    assert flags==['--headless']
    path=ROOT/'data/settings.json'
    initial=json.loads(path.read_text())['practice_plan']['default_hours']
    log=ROOT/'runs.jsonl'
    with log.open('a') as file: file.write(json.dumps(initial)+'\\n')
    if initial==6:
        (ROOT/'started').touch()
        until=time.monotonic()+15
        while not (ROOT/'continue').exists() and time.monotonic()<until: time.sleep(.05)
    latest=json.loads(path.read_text())['practice_plan']['default_hours']
    return 0 if initial==latest else 3
''')
        path = root / 'data/settings.json'
        path.parent.mkdir()
        path.write_text('{"practice_plan":{"enabled":true,"default_hours":4}}')
        python = root / '.venv/Scripts/python.exe'
        python.parent.mkdir(parents=True)
        python.touch()
        runtime = SingleInstanceLock(root / 'data/booker-runtime.lock')
        assert runtime.acquire()
        processes = []
        real_popen = subprocess.Popen
        def launch(command, **kwargs):
            assert kwargs['creationflags'] == getattr(subprocess, 'CREATE_NO_WINDOW', 0)
            command[0] = sys.executable
            process = real_popen(command, **kwargs)
            processes.append(process)
            return process
        def save(hours):
            runs.update_preferences(lambda settings: settings['practice_plan'].update(default_hours=hours), path)
        try:
            with patch.object(runs, 'ROOT', root), patch.object(runs, 'SETTINGS', path), \
                 patch.object(runs.subprocess, 'Popen', side_effect=launch):
                save(5)
                process = processes[0]
                time.sleep(.4)
                save(6)
                assert not (root / 'started').exists()
                runtime.release()
                wait_for(lambda: (root / 'started').exists(), process)
                save(7)
                save(8)
                (root / 'continue').touch()
                assert process.wait(timeout=20) == 0
                assert [json.loads(line) for line in (root / 'runs.jsonl').read_text().splitlines()] == [6, 8]
                assert load_settings(path)[runs.KEY]['state'] == 'completed'
                assert len(processes) == 1, 'Duplicate dispatchers should be suppressed'
            print('PASS real hidden subprocess: busy wait, rapid-save coalescing, latest settings, one follow-up; no ASIMUT access')
        finally:
            runtime.release()
            (root / 'continue').touch()
            for process in processes:
                if process.poll() is None:
                    process.wait(timeout=20)


if __name__ == '__main__':
    check()
