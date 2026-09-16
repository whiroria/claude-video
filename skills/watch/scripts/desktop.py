"""Local desktop launcher. Tk is imported only when opening the UI."""
from __future__ import annotations
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import threading
import uuid
import webbrowser

SCRIPTS = Path(__file__).resolve().parent
SKILL = SCRIPTS.parent
OUTPUT = Path.home() / 'VideoEssayAnalyzer' / 'results'


def build_command(source, transcript, title, offline, output):
    if not source.strip():
        raise ValueError('動画ファイルまたはYouTube URLを指定してください。')
    remote = source.startswith(('https://', 'http://'))
    if not remote and not Path(source).is_file():
        raise ValueError('動画ファイルが見つかりません。「選ぶ」で指定してください。')
    if remote and offline:
        raise ValueError('ローカル計測では動画ファイルを選んでください。')
    if transcript and not Path(transcript).is_file():
        raise ValueError('字幕ファイルが見つかりません。')
    cmd = [sys.executable, '-X', 'utf8', '-c',
           'from vea.cli import main; raise SystemExit(main())', source,
           '--mode', 'standard', '--max-frames', '9', '--resolution', '320',
           '--no-save', '--out', str(output)]
    if transcript:
        cmd += ['--transcript', transcript]
    if title:
        cmd += ['--title', title]
    if offline:
        cmd += ['--offline']
    return cmd


def make_report(result, destination):
    data = json.loads(result.read_text(encoding='utf-8-sig'))
    payload = json.dumps(data, ensure_ascii=False).replace('&', '\\u0026').replace('<', '\\u003c').replace('>', '\\u003e').replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')
    viewer = (SKILL / 'result-viewer.html').read_text(encoding='utf-8')
    script = '<script type="application/json" id="desktop-data">' + payload + '</script><script>load([{name:"result.json",size:1,text:async()=>document.getElementById("desktop-data").textContent}]);</script>'
    destination.write_text(viewer.replace('</html>', script + '</html>'), encoding='utf-8')
    return data


def main():
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    root = tk.Tk()
    root.title('Video Essay Analyzer')
    root.geometry('820x730')
    root.minsize(720, 650)
    frame = ttk.Frame(root, padding=24)
    frame.pack(fill='both', expand=True)
    frame.columnconfigure(1, weight=1)
    ttk.Label(frame, text='動画を選んで分析する', font=('', 20, 'bold')).grid(row=0, column=0, columnspan=3, sticky='w', pady=(0, 16))
    values = {}
    def field(row, name, label, kind=None, secret=False):
        v = tk.StringVar(); values[name] = v
        ttk.Label(frame, text=label).grid(row=row, column=0, sticky='w', padx=(0, 12), pady=6)
        ttk.Entry(frame, textvariable=v, show='*' if secret else '').grid(row=row, column=1, sticky='ew', pady=6)
        if kind:
            def choose():
                path = filedialog.askopenfilename(filetypes=kind)
                if path: v.set(path)
            ttk.Button(frame, text='選ぶ', command=choose).grid(row=row, column=2, padx=(8, 0))
    field(1, 'source', '動画 / YouTube URL', [('動画', '*.mp4 *.mkv *.mov *.webm'), ('全ファイル', '*.*')])
    field(2, 'transcript', '字幕（任意）', [('字幕', '*.vtt *.srt *.txt'), ('全ファイル', '*.*')])
    field(3, 'title', '元の動画タイトル（任意）')
    field(4, 'key', 'OpenAI APIキー', secret=True)
    offline = tk.BooleanVar(value=False)
    ttk.Checkbutton(frame, text='ローカル計測のみ（AI台本分析なし・API利用なし）', variable=offline).grid(row=5, column=0, columnspan=3, sticky='w', pady=6)
    ttk.Label(frame, text='AI分析では字幕と抽出画像などをOpenAIへ送信し、API利用料が発生します。\nキーはこの起動中だけ使用します。空欄なら既存の設定を使います。', wraplength=740).grid(row=6, column=0, columnspan=3, sticky='w', pady=6)
    field(7, 'model', '音声モデル（任意）', [('YAMNet ONNX', '*.onnx')])
    field(8, 'labels', '音声ラベル（任意）', [('YAMNet class map', '*.csv')])
    ttk.Label(frame, text='音声の区間推定には上の2ファイルと numpy / onnxruntime が必要です。\n空欄でも、台本・画面切替・音量などの基本分析は実行できます。', wraplength=740).grid(row=9, column=0, columnspan=3, sticky='w', pady=6)
    status = tk.StringVar(value='動画を選択してください。字幕だけでの分析はできません。')
    ttk.Label(frame, textvariable=status, wraplength=740).grid(row=10, column=0, columnspan=3, sticky='w', pady=12)
    progress = ttk.Progressbar(frame, mode='indeterminate')
    progress.grid(row=11, column=0, columnspan=3, sticky='ew')
    events = queue.Queue()
    state = {'busy': False, 'report': None}
    def open_result():
        if state['report']: webbrowser.open(state['report'].as_uri())
    def open_folder():
        OUTPUT.mkdir(parents=True, exist_ok=True)
        if sys.platform == 'win32': os.startfile(str(OUTPUT))
        else: webbrowser.open(OUTPUT.as_uri())
    def run_job(cmd, env, output, model, labels, source):
        try:
            # Keep complete child logs off disk: an API error may echo part of a key.
            result = subprocess.run(cmd, cwd=SCRIPTS, env=env, capture_output=True, text=True, encoding='utf-8', errors='replace', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if result.returncode or not output.exists():
                detail = '処理を完了できませんでした。動画・字幕・ffmpegの設定を確認してください。'
                if 'cp932' in result.stderr: detail = '文字コードの処理に失敗しました。'
                if 'ffmpeg' in result.stdout + result.stderr: detail += ' ffmpegが利用できるか確認してください。'
                raise RuntimeError(detail)
            # Keep credential fragments out of the delivered JSON and HTML.
            data = json.loads(output.read_text(encoding='utf-8'))
            for error in data.get('errors', []):
                message = str(error.get('message', ''))
                if 'Incorrect API key provided' in message:
                    message = 'OpenAI APIキーが拒否されました。キーを確認してください。'
                elif env.get('OPENAI_API_KEY'):
                    message = message.replace(env['OPENAI_API_KEY'], '[API key removed]')
                error['message'] = message
            output.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
            if model:
                events.put(('progress', '音声・音楽の区間を推定中です…'))
                data = json.loads(output.read_text(encoding='utf-8'))
                duration = data['metadata']['duration_ms'] / 1000
                audio_out = output.with_name('audio-result.json')
                audio = subprocess.run([sys.executable, '-X', 'utf8', str(SCRIPTS / 'vea' / 'audio_timeline.py'), source, '--result', str(output), '--model', model, '--labels', labels, '--seconds', str(duration), '--out', str(audio_out)], cwd=SCRIPTS, env=env, capture_output=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                if audio.returncode == 0: output = audio_out
                else: events.put(('note', '音声の区間推定は失敗しました。基本分析の結果を表示します。'))
            report = output.parent / 'report.html'
            data = make_report(output, report)
            issues = [str(e.get('module', '処理')) for e in data.get('errors', [])]
            ai_ok = data.get('modules', {}).get('script', {}).get('status') == 'ok'
            message = ('分析が終了しました。AI台本分析：' + ('完了' if ai_ok else '未完了 / 未実行') + '。')
            if issues: message += ' エラーのある項目：' + ', '.join(issues) + '。'
            if data.get('status') == 'partial': message += ' 一部の分析は未実施です。詳細は結果画面で確認できます。'
            events.put(('done', (report, message)))
        except Exception as exc:
            # Do not display upstream exception bodies containing credentials.
            events.put(('error', str(exc) if isinstance(exc, RuntimeError) else '処理中に問題が発生しました。入力ファイルと必要なソフトを確認してください。'))
    def start():
        if state['busy']: return
        try:
            v = {k: value.get().strip() for k, value in values.items()}
            if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
                raise ValueError('ffmpeg / ffprobeが見つかりません。インストール済みならアプリを開き直してください。')
            output = OUTPUT / uuid.uuid4().hex / 'result.json'
            cmd = build_command(v['source'], v['transcript'], v['title'], offline.get(), output)
            if bool(v['model']) != bool(v['labels']): raise ValueError('音声モデルと音声ラベルは両方選んでください。')
            if v['model']:
                if v['source'].startswith(('http://', 'https://')): raise ValueError('音声の区間推定を使う場合はローカル動画を選んでください。')
                if not Path(v['model']).is_file() or not Path(v['labels']).is_file(): raise ValueError('音声モデルまたはラベルのファイルが見つかりません。')
            from openai_analysis import load_openai_api_key
            key = v['key'] or load_openai_api_key()
            if not offline.get() and not key: raise ValueError('APIキーを入力するか「ローカル計測のみ」にチェックしてください。')
            env = dict(os.environ, PYTHONUTF8='1')
            if key: env['OPENAI_API_KEY'] = key
            output.parent.mkdir(parents=True, exist_ok=True)
            state['busy'] = True; state['note'] = ''
            start_button.config(state='disabled'); result_button.config(state='disabled')
            status.set('分析中です。動画の取得・映像の計測・AIの応答待ちには数分以上かかることがあります。')
            progress.start()
            threading.Thread(target=run_job, args=(cmd, env, output, v['model'], v['labels'], v['source']), daemon=True).start()
        except ValueError as exc: messagebox.showerror('入力を確認してください', str(exc))
    start_button = ttk.Button(frame, text='分析する', command=start)
    start_button.grid(row=12, column=0, pady=16, sticky='w')
    result_button = ttk.Button(frame, text='結果を開く', command=open_result, state='disabled')
    result_button.grid(row=12, column=1, pady=16, sticky='w')
    ttk.Button(frame, text='保存先を開く', command=open_folder).grid(row=12, column=2, pady=16)
    ttk.Label(frame, text='結果の保存先：' + str(OUTPUT), wraplength=740).grid(row=13, column=0, columnspan=3, sticky='w')
    def poll():
        while not events.empty():
            kind, value = events.get()
            if kind == 'progress': status.set(value)
            elif kind == 'note': state['note'] = value
            else:
                state['busy'] = False; progress.stop(); start_button.config(state='normal')
                if kind == 'done':
                    state['report'], text = value
                    status.set(text + ' ' + state.get('note', ''))
                    result_button.config(state='normal'); open_result()
                else: status.set(value)
        root.after(200, poll)
    def close():
        if state['busy']:
            messagebox.showinfo('分析中', '分析が終了してから閉じてください。'); return
        root.destroy()
    root.protocol('WM_DELETE_WINDOW', close)
    poll(); root.mainloop()

if __name__ == '__main__': main()
