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
import tempfile
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
    open_rhythm = 'document.getElementById("tab-media").click();' if data.get('editing_review') and 'script' not in data.get('modules', {}) else ''
    script = '<script type="application/json" id="desktop-data">' + payload + '</script><script>load([{name:"result.json",size:1,text:async()=>document.getElementById("desktop-data").textContent}]).then(()=>{' + open_rhythm + '});</script>'
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
    rhythm_settings = ttk.Frame(pages, padding=12)
    rhythm_settings.columnconfigure(1, weight=1)
    pages.add(rhythm_settings, text='編集リズム（新方式）')
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
    translate_subtitles = tk.BooleanVar(value=True)
    ttk.Checkbutton(settings, text='字幕を日本語に翻訳して表示（原文も保持・翻訳時は追加API処理）', variable=translate_subtitles).grid(row=10, column=0, columnspan=3, sticky='w', pady=6)
    rhythm_mode = tk.StringVar(value='従来：画面全体・感度低')
    ttk.Label(settings, text='画面切替の検出').grid(row=8, column=0, sticky='w', pady=6)
    ttk.Combobox(settings, textvariable=rhythm_mode, values=('従来：画面全体・感度低', '画面全体・感度高', '下部字幕あり：上75%・感度高'), state='readonly', width=32).grid(row=8, column=1, sticky='ew')
    ttk.Label(settings, text='下部字幕あり：画面の下25%を除外します。感度を上げると動きの誤検出も増えます。', wraplength=690).grid(row=9, column=0, columnspan=3, sticky='w')
    ttk.Label(rhythm_settings, text='背景の切替候補だけを保存します。部分画像の追加・字幕・パンやズームは記録対象外です。\nこのタブの「編集リズムだけ解析」はAPIキー・字幕が不要です。動画ファイルを選んで使います。', wraplength=690).grid(row=0, column=0, columnspan=3, sticky='w', pady=10)
    field(rhythm_settings, 1, 'rhythm_weights', '切替検出モデル', [('TransNet V2 weights', '*.npz')])
    for candidate in (SKILL / 'models' / 'transnetv2-weights.npz', Path.home() / 'Downloads' / 'transnetv2-weights.npz'):
        if candidate.is_file():
            values['rhythm_weights'].set(str(candidate)); break
    rhythm_region = tk.StringVar(value='下部字幕を除外（上80%）')
    ttk.Label(rhythm_settings, text='確認する画面の範囲').grid(row=2, column=0, sticky='w', pady=6)
    ttk.Combobox(rhythm_settings, textvariable=rhythm_region, values=('画面全体', '下部字幕を除外（上80%）'), state='readonly').grid(row=2, column=1, sticky='ew')
    ttk.Label(rhythm_settings, text='下部20%を除外すると、その領域の画像変化も対象外です。字幕が重ならない動画では「画面全体」を選んでください。', wraplength=690).grid(row=3, column=0, columnspan=3, sticky='w', pady=6)
    use_rhythm = tk.BooleanVar(value=bool(values['rhythm_weights'].get()))
    ttk.Checkbutton(rhythm_settings, text='通常の「分析する」にも新方式の編集リズムを追加', variable=use_rhythm).grid(row=4, column=0, columnspan=3, sticky='w', pady=6)
    ttk.Label(rhythm_settings, text='初回は「必要なソフトを準備」を押してください。インターネットから解析用の追加ソフトを取得します。\n切替検出モデルは配布された transnetv2-weights.npz を選択します。解析自体はPC内で行います。\nCPUで処理するため、長い動画は数分以上かかります。検出結果は未確認の候補です。', wraplength=690).grid(row=5, column=0, columnspan=3, sticky='w', pady=10)
    detailed = tk.BooleanVar(value=False)
    ttk.Checkbutton(inputs, text='全編詳細分析（60秒ごとに4枚・追加API料金と待ち時間が発生）', variable=detailed).grid(row=5, column=0, columnspan=3, sticky='w', pady=6)
    status = tk.StringVar(value='動画ファイルかYouTube URLを指定してください。字幕は選択中のタブの入力だけを使います。')
    ttk.Label(frame, textvariable=status, wraplength=740).grid(row=10, column=0, columnspan=3, sticky='w', pady=12)
    progress = ttk.Progressbar(frame, mode='indeterminate')
    progress.grid(row=11, column=0, columnspan=3, sticky='ew')
    events = queue.Queue()
    state = {'busy': False, 'report': None, 'job': None, 'closing': False}
    def open_result():
        if state['report']: webbrowser.open(state['report'].as_uri())
    def open_comparison():
        webbrowser.open((SKILL / 'result-viewer.html').as_uri() + '#compare')
    def open_folder():
        OUTPUT.mkdir(parents=True, exist_ok=True)
        if sys.platform == 'win32': os.startfile(str(OUTPUT))
        else: webbrowser.open(OUTPUT.as_uri())
    def launch(config, env):
        from desktop_jobs import Job
        job = Job()
        state['job'] = job
        cancel_button.config(state='normal')
        threading.Thread(target=job.run, args=(config, env, events), daemon=True).start()

    def cancel():
        job = state.get('job')
        if not state['busy'] or job is None: return
        cancel_button.config(state='disabled')
        status.set('中止しています。動画取得・解析の子プロセスも停止します…')
        def stop():
            try:
                job.cancel()
            except (OSError, subprocess.SubprocessError):
                events.put(('stop_failed', '停止できませんでした。「分析を中止」を再度押してください。'))
        threading.Thread(target=stop, daemon=True).start()

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
            rhythm_options = get_rhythm_options() if use_rhythm.get() else None
            env = dict(os.environ, PYTHONUTF8='1')
            env['WATCH_TRANSLATE_SUBTITLES'] = '1' if translate_subtitles.get() else '0'
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
            launch({'kind': 'analysis', 'args': dict(cmd=cmd, output=str(output), model=v['model'], labels=v['labels'], source=v['source'], reference_url=v['url'], use_detailed=detailed.get(), rhythm_options=rhythm_options)}, env)
        except (ValueError, OSError) as exc: messagebox.showerror('入力を確認してください', str(exc))

    def get_rhythm_options():
        import importlib.util
        if any(importlib.util.find_spec(name) is None for name in ('numpy', 'cv2', 'torch')):
            raise ValueError('「編集リズム（新方式）」タブの「必要なソフトを準備」を先に押してください。')
        weights = values['rhythm_weights'].get().strip()
        if not Path(weights).is_file():
            raise ValueError('切替検出モデルに transnetv2-weights.npz を選んでください。')
        return weights, (.8 if rhythm_region.get().startswith('下部') else 1.0)

    def rhythm_only():
        if state['busy']: return
        try:
            options = get_rhythm_options()
            video = values['video'].get().strip()
            if not Path(video).is_file():
                raise ValueError('「動画・字幕」タブで動画ファイルを選んでください。')
            if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
                raise ValueError('ffmpeg / ffprobeが見つかりません。')
        except (ValueError, OSError) as exc:
            messagebox.showerror('入力を確認してください', str(exc)); return
        output = OUTPUT / uuid.uuid4().hex / 'rhythm-result.json'
        state['busy'] = True; state['note'] = ''
        start_button.config(state='disabled'); result_button.config(state='disabled')
        progress.start(); status.set('編集リズムを解析しています。API料金は発生しません。')
        launch({'kind': 'rhythm', 'video': video, 'options': options, 'output': str(output)}, dict(os.environ, PYTHONUTF8='1'))

    def setup_rhythm():
        if state['busy']: return
        state['busy'] = True; state['note'] = ''
        start_button.config(state='disabled'); progress.start()
        status.set('解析用の追加ソフトを取得しています。初回は数分かかることがあります。')
        launch({'kind': 'setup'}, dict(os.environ, PYTHONUTF8='1'))
    ttk.Button(rhythm_settings, text='必要なソフトを準備（初回のみ）', command=setup_rhythm).grid(row=6, column=0, columnspan=2, sticky='w', pady=8)
    ttk.Button(rhythm_settings, text='編集リズムだけ解析', command=rhythm_only).grid(row=7, column=0, columnspan=2, sticky='w', pady=8)
    cancel_button = ttk.Button(frame, text='分析を中止', command=cancel, state='disabled')
    cancel_button.grid(row=14, column=0, pady=6, sticky='w')
    start_button = ttk.Button(frame, text='分析する', command=start)
    start_button.grid(row=12, column=0, pady=16, sticky='w')
    result_button = ttk.Button(frame, text='結果を開く', command=open_result, state='disabled')
    result_button.grid(row=12, column=1, pady=16, sticky='w')
    ttk.Button(frame, text='動画を比較する', command=open_comparison).grid(row=14, column=1, pady=6, sticky='w')
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
            if kind == 'stop_failed':
                state['closing'] = False
                cancel_button.config(state='normal'); status.set(value)
                continue
            if kind in ('done', 'error', 'setup_done') and state.get('job') and state['job'].cancelled.is_set():
                kind, value = 'cancelled', ''
            if kind in ('progress', 'note') and state.get('job') and state['job'].cancelled.is_set(): continue
            if kind == 'progress': status.set(value)
            elif kind == 'note': state['note'] = value
            else:
                state['busy'] = False; progress.stop(); start_button.config(state='normal')
                cancel_button.config(state='disabled')
                state['job'] = None
                if state['closing']:
                    root.destroy(); return
                if kind == 'cancelled':
                    status.set('処理を中止しました。完了済みの結果は保存先に残ります。送信済みAPI処理の料金は取り消せない場合があります。')
                    if state['report']: result_button.config(state='normal')
                elif kind == 'setup_done':
                    status.set(value)
                elif kind == 'done':
                    report, text = value
                    state['report'] = Path(report)
                    status.set(text + ' ' + state.get('note', ''))
                    result_button.config(state='normal'); open_result()
                else:
                    status.set(value.splitlines()[0] + ' 詳細ウィンドウの「詳細をコピー」で内容を共有できます。')
                    show_failure(value)
        root.after(200, poll)
    def close():
        if state['busy']:
            state['closing'] = True
            cancel(); return
        root.destroy()
    root.protocol('WM_DELETE_WINDOW', close)
    poll(); root.mainloop()

if __name__ == '__main__': main()
