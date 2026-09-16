"""Local desktop launcher. Tk is imported only when opening the UI."""
from __future__ import annotations
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
import threading
import uuid
import webbrowser
from urllib.parse import urlparse

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


def failure_details(stdout, stderr, api_key=''):
    messages = []
    for line in stdout.splitlines():
        try:
            data = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict) and data.get('error'):
            messages.append(str(data['error']))
    raw = '\n'.join(messages + [stderr[-8000:]])
    if api_key:
        raw = raw.replace(api_key, '[APIキー非表示]')
    raw = re.sub(r'(?im)^.*(?:incorrect api key|authorization:|api[_ -]?key\s*[=:]).*$', '[認証情報を含む行は非表示]', raw)
    raw = re.sub(r'sk-[A-Za-z0-9_*.-]+', '[APIキー非表示]', raw)
    lower = raw.lower()
    if 'yt-dlp is not installed' in lower:
        hint = '動画取得ソフト yt-dlp が見つかりません。'
    elif 'sign in' in lower or 'not a bot' in lower:
        hint = 'YouTube側でログイン確認などが求められ、動画を取得できませんでした。'
    elif '403' in lower or '429' in lower:
        hint = '取得先またはAPIがリクエストを拒否しました。詳細の発生元を確認してください。'
    elif 'private video' in lower or 'video unavailable' in lower:
        hint = '指定した動画を取得できませんでした。公開状態とURLを確認してください。'
    else:
        hint = '処理を完了できませんでした。以下の詳細を確認してください。'
    return hint + '\n\n' + (raw.strip() or '詳細が取得できませんでした。')[-8000:]


def selected_source(video, url):
    video, url = video.strip(), url.strip()
    if not video and not url:
        raise ValueError('動画ファイルまたはYouTube URLを入力してください。')
    if url:
        parsed = urlparse(url)
        if parsed.scheme not in ('https', 'http') or not parsed.hostname:
            raise ValueError('URL欄には https:// から始まる動画URLを入力してください。')
    if video.startswith(('https://', 'http://')):
        raise ValueError('動画ファイル欄には「選ぶ」でファイルを指定してください。')
    return video or url


def attach_reference(data, url):
    if url:
        data.setdefault('metadata', {})['user_reference_url'] = url
        data['metadata']['user_reference_url_note'] = 'ユーザー指定の参照URL。動画ファイルとの一致は未確認。'
    return data


def save_pasted_transcript(text, directory):
    text = text.lstrip('\ufeff').strip()
    if not text:
        raise ValueError('字幕を貼り付けるか、「字幕ファイル」のタブを選んでください。')
    # Preserve timing when full SRT timecodes or WEBVTT are pasted.
    is_srt = bool(re.search(r'(?m)^\d{2}:\d{2}:\d{2},\d{3}\s+-->\s+\d{2}:\d{2}:\d{2},\d{3}', text))
    if is_srt:
        text = re.sub(r'(\d{2}:\d{2}:\d{2}),(\d{3})', r'\1.\2', text)
        text = 'WEBVTT\n\n' + text
    suffix = '.vtt' if text.startswith('WEBVTT') else '.txt'
    path = directory / ('pasted-transcript' + suffix)
    directory.mkdir(parents=True, exist_ok=True)
    path.write_text(text + '\n', encoding='utf-8')
    return str(path)


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
    root.geometry('850x760')
    root.minsize(720, 650)
    frame = ttk.Frame(root, padding=20)
    frame.pack(fill='both', expand=True)
    frame.columnconfigure(1, weight=1)
    ttk.Label(frame, text='動画を選んで分析する', font=('', 20, 'bold')).grid(row=0, column=0, columnspan=3, sticky='w', pady=(0, 12))
    pages = ttk.Notebook(frame)
    pages.grid(row=1, column=0, columnspan=3, sticky='nsew')
    frame.rowconfigure(1, weight=1)
    inputs, settings = ttk.Frame(pages, padding=12), ttk.Frame(pages, padding=12)
    pages.add(inputs, text='動画・字幕')
    pages.add(settings, text='API・音声設定')
    for parent in (inputs, settings): parent.columnconfigure(1, weight=1)
    values = {}
    def field(parent, row, name, label, kind=None, secret=False):
        v = tk.StringVar(); values[name] = v
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky='w', padx=(0, 12), pady=6)
        entry = ttk.Entry(parent, textvariable=v, show='*' if secret else '')
        entry.grid(row=row, column=1, sticky='ew', pady=6)
        button = None
        if kind:
            def choose():
                path = filedialog.askopenfilename(filetypes=kind)
                if path: v.set(path)
            button = ttk.Button(parent, text='選ぶ', command=choose)
            button.grid(row=row, column=2, padx=(8, 0))
        return entry, button
    ttk.Label(inputs, text='片方だけでも、両方でも入力できます。両方ある場合は動画ファイルを分析し、URLを参照先として保存します。', wraplength=690).grid(row=0, column=0, columnspan=3, sticky='w', pady=6)
    field(inputs, 1, 'video', '動画ファイル（任意）', [('動画', '*.mp4 *.mkv *.mov *.webm'), ('全ファイル', '*.*')])
    field(inputs, 2, 'url', 'YouTube URL（任意）')
    field(inputs, 3, 'title', '元の動画タイトル（任意）')
    subtitles = ttk.Notebook(inputs)
    subtitles.grid(row=4, column=0, columnspan=3, sticky='nsew', pady=10)
    inputs.rowconfigure(4, weight=1)
    sub_file, sub_paste = ttk.Frame(subtitles, padding=8), ttk.Frame(subtitles, padding=8)
    subtitles.add(sub_file, text='字幕ファイル（任意）')
    subtitles.add(sub_paste, text='字幕をコピペ')
    sub_file.columnconfigure(1, weight=1)
    field(sub_file, 0, 'transcript', '字幕ファイル', [('字幕', '*.vtt *.srt *.txt'), ('全ファイル', '*.*')])
    ttk.Label(sub_file, text='SRT・VTT・TXTに対応。空欄なら既存の字幕取得・文字起こし処理を使います。', wraplength=690).grid(row=1, column=0, columnspan=3, sticky='w', pady=8)
    from tkinter.scrolledtext import ScrolledText
    pasted = ScrolledText(sub_paste, height=6, wrap='word', undo=True)
    pasted.pack(fill='both', expand=True)
    ttk.Label(sub_paste, text='Ctrl+Vで貼り付け。通常の文章・SRT・VTTに対応。時刻のない文章は正確な時刻照合に使えません。', wraplength=690).pack(anchor='w', pady=(6, 0))
    field(settings, 0, 'key', 'OpenAI APIキー', secret=True)
    offline = tk.BooleanVar(value=False)
    ttk.Checkbutton(settings, text='ローカル計測のみ（AI台本分析なし・API利用なし）', variable=offline).grid(row=1, column=0, columnspan=3, sticky='w', pady=6)
    ttk.Label(settings, text='AI分析では字幕と抽出画像などをOpenAIへ送信し、API利用料が発生します。\nキーはこの起動中だけ使用します。空欄なら既存の設定を使います。', wraplength=690).grid(row=2, column=0, columnspan=3, sticky='w', pady=6)
    field(settings, 3, 'model', '音声モデル（任意）', [('YAMNet ONNX', '*.onnx')])
    field(settings, 4, 'labels', '音声ラベル（任意）', [('YAMNet class map', '*.csv')])
    ttk.Label(settings, text='音声の区間推定には上の2ファイルと numpy / onnxruntime が必要です。\n空欄でも、台本・画面切替・音量などの基本分析は実行できます。', wraplength=690).grid(row=5, column=0, columnspan=3, sticky='w', pady=6)
    speech_language = tk.StringVar(value='自動判定')
    ttk.Label(settings, text='自動文字起こしの音声言語').grid(row=6, column=0, sticky='w', pady=6)
    ttk.Combobox(settings, textvariable=speech_language, values=('自動判定', '日本語', '英語'), state='readonly').grid(row=6, column=1, sticky='ew')
    ttk.Label(settings, text='日本語音声なら「日本語」を選択。字幕ファイル・コピペ・取得済み字幕はそのまま使います。', wraplength=690).grid(row=7, column=0, columnspan=3, sticky='w')
    rhythm_mode = tk.StringVar(value='従来：画面全体・感度低')
    ttk.Label(settings, text='画面切替の検出').grid(row=8, column=0, sticky='w', pady=6)
    ttk.Combobox(settings, textvariable=rhythm_mode, values=('従来：画面全体・感度低', '画面全体・感度高', '下部字幕あり：上75%・感度高'), state='readonly', width=32).grid(row=8, column=1, sticky='ew')
    ttk.Label(settings, text='下部字幕あり：画面の下25%を除外します。感度を上げると動きの誤検出も増えます。', wraplength=690).grid(row=9, column=0, columnspan=3, sticky='w')
    detailed = tk.BooleanVar(value=False)
    ttk.Checkbutton(inputs, text='全編詳細分析（60秒ごとに4枚・追加API料金と待ち時間が発生）', variable=detailed).grid(row=5, column=0, columnspan=3, sticky='w', pady=6)
    status = tk.StringVar(value='動画ファイルかYouTube URLを指定してください。字幕は選択中のタブの入力だけを使います。')
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
    def run_job(cmd, env, output, model, labels, source, reference_url, use_detailed):
        try:
            # Keep complete child logs off disk: an API error may echo part of a key.
            result = subprocess.run(cmd, cwd=SCRIPTS, env=env, capture_output=True, text=True, encoding='utf-8', errors='replace', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if result.returncode or not output.exists():
                raise RuntimeError(failure_details(result.stdout, result.stderr, env.get('OPENAI_API_KEY', '')))
            # Keep credential fragments out of the delivered JSON and HTML.
            data = json.loads(output.read_text(encoding='utf-8'))
            from vea.detailed_review import attach_previews, review
            attach_previews(data)
            attach_reference(data, reference_url)
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
            if use_detailed:
                data = json.loads(output.read_text(encoding='utf-8'))
                frames = [e for e in data.get('timeline', []) if e.get('event_type') == 'frame']
                video = frames[0].get('provenance', {}).get('source') if frames else None
                if video and Path(video).is_file():
                    data = review(data, video, output, lambda text: events.put(('progress', text)), api_key=env.get('OPENAI_API_KEY'))
                    if any(e['status'] != 'ok' for e in data['detailed_review']['intervals']):
                        events.put(('note', '全編詳細分析に未完了の区間があります。結果画面で確認してください。'))
                else:
                    events.put(('note', '全編詳細分析用の動画が見つかりません。基本分析を表示します。'))
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
            v['source'] = selected_source(v['video'], v['url'])
            use_paste = subtitles.index(subtitles.select()) == 1
            # Validate the selected source before creating any pasted file.
            cmd = build_command(v['source'], '' if use_paste else v['transcript'], v['title'], offline.get(), output)
            if use_paste and not pasted.get('1.0', 'end-1c').strip():
                raise ValueError('「字幕をコピペ」の欄に文章を貼り付けてください。')
            if bool(v['model']) != bool(v['labels']): raise ValueError('音声モデルと音声ラベルは両方選んでください。')
            if v['model']:
                if v['source'].startswith(('http://', 'https://')): raise ValueError('音声の区間推定を使う場合はローカル動画を選んでください。')
                if not Path(v['model']).is_file() or not Path(v['labels']).is_file(): raise ValueError('音声モデルまたはラベルのファイルが見つかりません。')
            from openai_analysis import load_openai_api_key
            key = v['key'] or load_openai_api_key()
            if not offline.get() and not key: raise ValueError('APIキーを入力するか「ローカル計測のみ」にチェックしてください。')
            if detailed.get() and offline.get():
                raise ValueError('全編詳細分析はAIを使います。「ローカル計測のみ」を外してください。')
            env = dict(os.environ, PYTHONUTF8='1')
            env['WATCH_TRANSCRIPT_LANGUAGE'] = {'自動判定': 'auto', '日本語': 'ja', '英語': 'en'}[speech_language.get()]
            if key: env['OPENAI_API_KEY'] = key
            output.parent.mkdir(parents=True, exist_ok=True)
            if use_paste:
                transcript_path = save_pasted_transcript(pasted.get('1.0', 'end-1c'), output.parent)
                cmd = build_command(v['source'], transcript_path, v['title'], offline.get(), output)
            if rhythm_mode.get() != '従来：画面全体・感度低':
                cmd += ['--scene-threshold', '0.10']
            env['WATCH_SCENE_TOP_FRACTION'] = '0.75' if rhythm_mode.get().startswith('下部字幕あり') else '1'
            state['busy'] = True; state['note'] = ''
            start_button.config(state='disabled'); result_button.config(state='disabled')
            status.set('分析中です。動画の取得・映像の計測・AIの応答待ちには数分以上かかることがあります。')
            progress.start()
            threading.Thread(target=run_job, args=(cmd, env, output, v['model'], v['labels'], v['source'], v['url'], detailed.get()), daemon=True).start()
        except (ValueError, OSError) as exc: messagebox.showerror('入力を確認してください', str(exc))
    start_button = ttk.Button(frame, text='分析する', command=start)
    start_button.grid(row=12, column=0, pady=16, sticky='w')
    result_button = ttk.Button(frame, text='結果を開く', command=open_result, state='disabled')
    result_button.grid(row=12, column=1, pady=16, sticky='w')
    ttk.Button(frame, text='保存先を開く', command=open_folder).grid(row=12, column=2, pady=16)
    ttk.Label(frame, text='結果の保存先：' + str(OUTPUT), wraplength=740).grid(row=13, column=0, columnspan=3, sticky='w')
    def show_failure(detail):
        window = tk.Toplevel(root)
        window.title('エラーの詳細')
        window.geometry('760x420')
        box = ScrolledText(window, wrap='word')
        box.pack(fill='both', expand=True, padx=12, pady=12)
        box.insert('1.0', detail)
        box.configure(state='disabled')
        def copy():
            root.clipboard_clear()
            root.clipboard_append(detail)
        ttk.Button(window, text='詳細をコピー', command=copy).pack(pady=(0, 12))

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
                else:
                    status.set(value.splitlines()[0] + ' 詳細ウィンドウの「詳細をコピー」で内容を共有できます。')
                    show_failure(value)
        root.after(200, poll)
    def close():
        if state['busy']:
            messagebox.showinfo('分析中', '分析が終了してから閉じてください。'); return
        root.destroy()
    root.protocol('WM_DELETE_WINDOW', close)
    poll(); root.mainloop()

if __name__ == '__main__': main()
