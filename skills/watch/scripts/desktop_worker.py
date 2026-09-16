"""Isolated desktop worker: all analysis descendants belong to one cancellable job."""
import json
import os
from pathlib import Path
import subprocess
import sys
from desktop import SCRIPTS, failure_details, attach_reference, make_report

class Events:
    def put(self, event):
        print(json.dumps(event, ensure_ascii=False, default=str), flush=True)

events = Events()

def run_job(cmd, env, output, model, labels, source, reference_url, use_detailed, rhythm_options=None):
    output = Path(output)
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
        if rhythm_options:
            data = json.loads(output.read_text(encoding='utf-8'))
            frames = [e for e in data.get('timeline', []) if e.get('event_type') == 'frame']
            local_video = source if Path(source).is_file() else (frames[0].get('provenance', {}).get('source') if frames else None)
            if not local_video or not Path(local_video).is_file():
                raise RuntimeError('編集リズムを照合する動画が見つかりません。動画ファイルを指定してください。')
            from vea.rhythm import review
            data['editing_review'] = review(local_video, *rhythm_options, progress=lambda text: events.put(('progress', text)))
            data['modules']['editing_review'] = {'status': 'ok'}
            output.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
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

def main():
    config = json.loads(sys.stdin.read())
    if config['kind'] == 'analysis':
        run_job(env=dict(os.environ), **config['args'])
    elif config['kind'] == 'rhythm':
        from vea.rhythm import review
        output = Path(config['output'])
        video = config['video']
        from vea.rhythm import probe
        from uuid import uuid4
        data = review(video, *config['options'], progress=lambda text: events.put(('progress', text)))
        duration, _ = probe(video)
        result = dict(run_id=str(uuid4()), metadata=dict(title=Path(video).stem, duration_ms=round(duration*1000)),
                      modules={'editing_review': {'status': 'ok'}}, editing_review=data,
                      status='partial', timeline=[], features={}, errors=[],
                      warnings=['編集リズムのみの解析です。台本・音声のAI分析は実行していません。'])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
        make_report(output, output.with_suffix('.html'))
        events.put(('done', (output.with_suffix('.html'), '背景切替候補の解析が終了しました。')))
    elif config['kind'] == 'setup':
        commands = [[sys.executable, '-m', 'pip', 'install', 'numpy', 'opencv-python-headless'],
                    [sys.executable, '-m', 'pip', 'install', 'torch>=2.6', '--index-url', 'https://download.pytorch.org/whl/cpu']]
        for command in commands:
            result = subprocess.run(command, capture_output=True, timeout=1800)
            if result.returncode:
                raise RuntimeError('追加ソフトの準備に失敗しました。再度「必要なソフトを準備」を押してください。')
        events.put(('setup_done', '準備できました。切替検出モデルを選択してください。'))

if __name__ == '__main__':
    try:
        main()
    except (Exception, SystemExit):
        events.put(('error', '処理中に問題が発生しました。入力ファイルと必要なソフトを確認してください。'))
