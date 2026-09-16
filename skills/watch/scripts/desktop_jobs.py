"""Own a process tree per desktop task; cancellation never targets other jobs."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading

SCRIPTS = Path(__file__).resolve().parent


def stop_tree(process):
    if os.name == 'nt':
        if process.poll() is not None:
            return
        result = subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                                capture_output=True, timeout=15,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode and process.poll() is None:
            raise OSError('Could not stop analysis process tree')
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


class Job:
    def __init__(self):
        self.cancelled = threading.Event()
        self.lock = threading.Lock()
        self.process = None

    def cancel(self):
        # Set first so cancellation requested before Popen prevents a launch.
        self.cancelled.set()
        with self.lock:
            if self.process is not None:
                stop_tree(self.process)

    def run(self, config, env, events, command=None):
        terminal = ('error', '解析プロセスが終了しました。入力と設定を確認してください。')
        p = None
        try:
            with self.lock:
                if self.cancelled.is_set():
                    events.put(('cancelled', '')); return
                p = subprocess.Popen(
                    command or [sys.executable, '-X', 'utf8', '-u', str(SCRIPTS / 'desktop_worker.py')],
                    cwd=SCRIPTS, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, text=True, encoding='utf-8', errors='replace',
                    start_new_session=os.name != 'nt',
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                )
                self.process = p
            # Configuration travels in a pipe. API keys are only in the child env.
            p.stdin.write(json.dumps(config, ensure_ascii=False))
            p.stdin.close()
            for line in p.stdout:
                try:
                    event = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if not isinstance(event, list) or len(event) != 2:
                    continue
                if event[0] in ('done', 'error', 'setup_done'):
                    terminal = tuple(event)
                elif event[0] in ('progress', 'note') and not self.cancelled.is_set():
                    events.put(tuple(event))
            code = p.wait()
            if code and terminal[0] != 'error':
                terminal = ('error', '処理を完了できませんでした。')
        except (OSError, ValueError):
            terminal = ('error', '解析プロセスを開始・実行できませんでした。')
        finally:
            if p is not None:
                # A pipe failure must not leave the analysis running invisibly.
                if p.poll() is None:
                    try:
                        stop_tree(p)
                    except (OSError, subprocess.SubprocessError):
                        events.put(('stop_failed', '停止できませんでした。「分析を中止」を再度押してください。'))
                p.wait()
                for stream in (p.stdin, p.stdout):
                    if stream and not stream.closed:
                        stream.close()
            with self.lock:
                self.process = None
        events.put(('cancelled', '') if self.cancelled.is_set() else terminal)
