# Dependencies and license review

Core v2 modules use Python standard library and external FFmpeg; pytest is test-only. Existing MIT copyright is unchanged. No third-party code or model weights are vendored. Python 3.10+; tested environment recorded in validation.md. FFmpeg/ffprobe and yt-dlp must be on PATH on Windows, macOS or Linux.

Optional local backends are opt-in and not part of default installation:

| Backend | Package | Code-license source | Deployment status |
|---|---|---|---|
| CLIP | torch, transformers, Pillow | https://github.com/openai/CLIP/blob/main/LICENSE (MIT); transformers has its own license | local HF model directory required; CPU supported; checkpoint license/revision recorded separately |
| PANNs | panns-inference, librosa | https://github.com/qiuqiangkong/panns_inference | explicit local Cnn14 checkpoint required; per-window speech/music only; weights/dataset terms require separate review |
| librosa beats | librosa | https://github.com/librosa/librosa/blob/main/LICENSE.md | optional local dependency; full-mix beat hypothesis |
| WhisperX | whisperx | https://github.com/m-bain/whisperX | local ASR+alignment connection implemented; real model integration test pending; do not install by default |
| TransNetV2 | TensorFlow/upstream inference | https://github.com/soCzech/TransNetV2 | local upstream module/weights adapter implemented; not bundled |
| Demucs | demucs | https://github.com/facebookresearch/demucs/blob/main/LICENSE | optional local-repository subprocess adapter; maintenance and weight licenses need deployment review |

Sources reviewed 2026-09-14. Code licenses do not establish weight or training-data rights. Actual heavy model files were unavailable in this environment, so no claim of tested package-version compatibility or GPU performance is made. The default path adds no heavy ML runtime. Existing OpenAI and Whisper API use is retained; YOUTUBE_API_KEY is optional for authoritative preceding-video catalogs. No API keys are committed; no live paid inference was run.
