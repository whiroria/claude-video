import json
import os
from pathlib import Path
import queue
import sys
import threading
import time
from types import SimpleNamespace

import pytest
from desktop_jobs import Job


def start(command, config=None):
    job, events = Job(), queue.Queue()
    thread = threading.Thread(target=job.run, args=(config or {}, dict(os.environ), events, command))
    thread.start()
    return job, events, thread


def test_cancel_before_launch_never_starts_process(monkeypatch):
    import desktop_jobs
    monkeypatch.setattr(desktop_jobs.subprocess, 'Popen', lambda *a, **k: pytest.fail('must not launch'))
    job, events = Job(), queue.Queue()
    job.cancel()
    job.run({}, {}, events)
    assert events.get_nowait()[0] == 'cancelled'
    assert events.empty()


def test_cancel_process_tree_and_allow_new_job():
    code = "import subprocess,sys,json,time;sys.stdin.read(); p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)']);print(json.dumps(['progress',str(p.pid)]),flush=True);time.sleep(120)"
    job, events, thread = start([sys.executable, '-u', '-c', code])
    event = events.get(timeout=10)
    assert event[0] == 'progress'
    child = int(event[1])
    job.cancel();thread.join(10)
    assert not thread.is_alive()
    assert events.get(timeout=1)[0] == 'cancelled'
    assert events.empty()
    if sys.platform.startswith('linux'):
        deadline=time.monotonic()+2
        while time.monotonic()<deadline:
            try: state=Path(f'/proc/{child}/stat').read_text().split()[2]
            except FileNotFoundError: break
            if state=='Z': break
            time.sleep(.02)
        else: pytest.fail('descendant is still running')
    next_job, events, thread = start([sys.executable, '-c', "import sys;sys.stdin.read();print('[\"setup_done\",\"ok\"]')"])
    thread.join(10)
    assert not thread.is_alive()
    assert events.get(timeout=1)==('setup_done','ok')


def test_windows_stops_only_owned_tree(monkeypatch):
    import desktop_jobs
    captured=[]
    monkeypatch.setattr(desktop_jobs, 'os', SimpleNamespace(name='nt'))
    monkeypatch.setattr(desktop_jobs.subprocess, 'run', lambda cmd, **kw: (captured.append(cmd) or SimpleNamespace(returncode=0)))
    desktop_jobs.stop_tree(SimpleNamespace(pid=12345, poll=lambda: None))
    assert captured == [['taskkill','/PID','12345','/T','/F']]


def test_worker_completes_normal_report_without_api(tmp_path):
    from desktop import SCRIPTS
    output=tmp_path/'result.json'
    payload={'metadata':{'title':'test','duration_ms':1000},'modules':{},'timeline':[], 'errors':[], 'warnings':[], 'status':'partial'}
    code="from pathlib import Path; Path("+repr(str(output))+").write_text("+repr(json.dumps(payload))+", encoding='utf-8')"
    config={'kind':'analysis','args':dict(cmd=[sys.executable,'-c',code],output=str(output),model='',labels='',source='test',reference_url='',use_detailed=False,rhythm_options=None)}
    job, events, thread=start([sys.executable,'-u',str(SCRIPTS/'desktop_worker.py')],config)
    thread.join(15)
    assert not thread.is_alive()
    terminal=events.get(timeout=1)
    assert terminal[0]=='done',terminal
    assert (tmp_path/'report.html').is_file()
