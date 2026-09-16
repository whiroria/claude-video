import json

from vea import subtitle_translation as translation


def test_preserve_timestamps_and_source_when_response_is_reordered(monkeypatch):
    source = '[00:03.350] Imagine an island.\n[00:09.050] Twice Japan.\n[00:12] 日本語です。'
    def api(**kw):
        assert kw['frame_paths'] == []
        entries = json.loads(kw['transcript'])
        assert all('00:' not in v['text'] for v in entries)
        return {'lines': [{'id': 1, 'text': '日本の2倍。'}, {'id': 0, 'text': '島を想像して。'}]}
    monkeypatch.setattr(translation, 'analyze_with_openai', api)
    result = translation.japanese_display(source, 'test')
    assert result['text_ja'] == '[00:03.350] 島を想像して。\n[00:09.050] 日本の2倍。\n[00:12] 日本語です。'
    assert 'Imagine' in source


def test_disabled_and_japanese_do_not_call_api(monkeypatch):
    def api(**kw):
        raise AssertionError('API should not be used')
    monkeypatch.setattr(translation, 'analyze_with_openai', api)
    assert translation.japanese_display('English', 'test', enabled=False)['status'] == 'skipped'
    assert translation.japanese_display('[00:01] 日本語です。', 'test')['status'] == 'not_needed'


def test_missing_or_duplicate_lines_never_exposes_half_translation(monkeypatch):
    monkeypatch.setattr(translation, 'analyze_with_openai', lambda **kw: {
        'lines': [{'id': 0, 'text': '訳'}, {'id': 0, 'text': '訳'}]})
    result = translation.japanese_display('First\nSecond', 'test')
    assert result['status'] == 'failed' and 'text_ja' not in result


def test_long_untimed_paste_is_bounded_and_failure_does_not_leak_credentials(monkeypatch):
    batches = []
    def api(**kw):
        entries = json.loads(kw['transcript'])
        batches.append(entries)
        assert sum(len(v['text']) for v in entries) <= 6000
        if len(batches) == 2:
            raise SystemExit('secret API response')
        return {'lines': [{'id': v['id'], 'text': '訳'} for v in entries]}
    monkeypatch.setattr(translation, 'analyze_with_openai', api)
    result = translation.japanese_display('Long text. ' * 1200, 'test')
    assert len(batches) == 2
    assert result['status'] == 'failed' and 'text_ja' not in result
    assert 'secret' not in str(result)
