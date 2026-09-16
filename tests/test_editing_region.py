import subprocess
from vea.extractors import FFmpegSceneDetector


def make_video(path, top_changes):
    frames = []
    for i in range(40):
        top = (64 if i < 20 else 96) if top_changes else 64
        bottom = 0 if top_changes or i < 20 else 255
        frames.append(bytes([top]) * (64*48) + bytes([bottom]) * (64*16))
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'rawvideo', '-pixel_format', 'gray',
                    '-video_size', '64x64', '-framerate', '10', '-i', '-', '-c:v', 'ffv1', str(path)],
                   input=b''.join(frames), check=True)


def test_fixed_lower_panel_and_subtle_background_cut(tmp_path):
    path = tmp_path / 'background.mkv'
    make_video(path, True)
    old = FFmpegSceneDetector().detect(path, 4000)
    new = FFmpegSceneDetector(.1, .75).detect(path, 4000)
    assert len(old) == 1
    assert len(new) == 2
    assert new[1]['start_ms'] == 2000


def test_lower_panel_changes_excluded(tmp_path):
    path = tmp_path / 'caption.mkv'
    make_video(path, False)
    assert len(FFmpegSceneDetector(.1).detect(path, 4000)) == 2
    assert len(FFmpegSceneDetector(.1, .75).detect(path, 4000)) == 1
