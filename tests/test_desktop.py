import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/watch/scripts'
spec = importlib.util.spec_from_file_location('desktop', SCRIPTS / 'desktop.py')
desktop = importlib.util.module_from_spec(spec)
spec.loader.exec_module(desktop)


def test_inputs_and_literal_paths(tmp_path):
    video = tmp_path / "a ' & b.mp4"
    video.touch()
    cmd = desktop.build_command(str(video), '', '', True, tmp_path / 'result.json')
    assert cmd[5] == str(video)
    assert '--offline' in cmd
    with pytest.raises(ValueError):
        desktop.build_command('', '', '', False, tmp_path / 'result.json')
    with pytest.raises(ValueError):
        desktop.build_command('https://youtu.be/test', '', '', True, tmp_path / 'result.json')


def test_report_escapes_script_injection(tmp_path):
    source = tmp_path / 'result.json'
    source.write_text(json.dumps({'title': '</script><script>alert(1)</script>'}))
    report = tmp_path / 'report.html'
    desktop.make_report(source, report)
    text = report.read_text()
    assert '</script><script>alert(1)' not in text
    assert '\\u003c/script' in text
    assert 'id="desktop-data"' in text


def test_real_offline_analysis_and_report(tmp_path):
    video = tmp_path / 'short clip.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=160x90:rate=10:duration=2', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=2', '-c:v', 'libx264', '-c:a', 'aac', '-shortest', str(video)], check=True)
    result = tmp_path / 'result.json'
    run = subprocess.run(desktop.build_command(str(video), '', 'テスト動画', True, result), cwd=SCRIPTS, capture_output=True, text=True)
    assert run.returncode == 0, run.stdout + run.stderr
    data = desktop.make_report(result, tmp_path / 'report.html')
    assert data['metadata']['title'] == 'テスト動画'
    assert data['metadata']['duration_ms'] > 0
    assert data['modules']['editing']['status'] == 'ok'


def test_source_selection_accepts_either_or_both(tmp_path):
    video = str(tmp_path / 'video.mp4')
    url = 'https://youtu.be/selected'
    assert desktop.selected_source(video, url) == video
    assert desktop.selected_source(video, '') == video
    assert desktop.selected_source('', url) == url
    with pytest.raises(ValueError):
        desktop.selected_source('', '')
    with pytest.raises(ValueError):
        desktop.selected_source(video, 'invalid-url')
    data = {'metadata': {'source': video, 'url': video}}
    desktop.attach_reference(data, url)
    assert data['metadata']['user_reference_url'] == url
    assert data['metadata']['source'] == video
    assert data['metadata']['url'] == video


def test_pasted_subtitles_preserve_timing_and_plain_text(tmp_path):
    sys.path.insert(0, str(SCRIPTS))
    from transcribe import parse_vtt
    srt = '1\n00:00:01,200 --> 00:00:02,500\nこんにちは。\n'
    path = Path(desktop.save_pasted_transcript(srt, tmp_path))
    assert path.suffix == '.vtt'
    segments = parse_vtt(str(path))
    assert len(segments) == 1
    assert segments[0]['text'] == 'こんにちは。'
    assert '00:00:01.200 --> 00:00:02.500' in path.read_text()
    text = '時刻のない字幕です。\n次の段落です。'
    plain = Path(desktop.save_pasted_transcript(text, tmp_path))
    assert plain.suffix == '.txt'
    assert plain.read_text().strip() == text
    with pytest.raises(ValueError):
        desktop.save_pasted_transcript('  ', tmp_path)


def test_failure_details_preserve_download_cause_and_hide_key():
    stdout = json.dumps({'status': 'failed', 'error': 'yt-dlp is not installed'})
    detail = desktop.failure_details(stdout, '')
    assert 'yt-dlp' in detail and '見つかりません' in detail
    detail = desktop.failure_details('', 'ERROR: Sign in to confirm you are not a bot')
    assert 'ログイン確認' in detail
    detail = desktop.failure_details('', 'Authorization: Bearer secret-value\nIncorrect API key provided: sk-masked***\nsecret-value', 'secret-value')
    assert 'secret-value' not in detail and 'sk-masked' not in detail
