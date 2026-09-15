# /watch

**Give Claude the ability to watch any video.**

Claude Code (recommended — auto-updates via marketplace):
```
/plugin marketplace add bradautomates/claude-video
/plugin install watch@claude-video
```

Codex, Cursor, Copilot, Gemini CLI, or any of 50+ [Agent Skills](https://agentskills.io) hosts:
```bash
npx skills add bradautomates/claude-video -g
```
(`-g` installs globally for your user, available across all projects. Drop it to scope per-project.)

More install options (claude.ai web, manual) in the [Install](#install) section below.

Zero config to start — `yt-dlp` and `ffmpeg` install on first run via `brew` on macOS (Linux/Windows print exact commands). Captions cover most public videos for free. Whisper API key is only needed when a video has no captions.

---

Claude can read a webpage, run a script, browse a repo. What it can't do, out of the box, is *watch a video*. You paste a YouTube link and it has to either guess from the title or pull a transcript that's missing 90% of what's on screen.

With Claude Video `/watch` you can paste a URL or a local path, ask a question, and Claude fetches captions first, downloads only what it needs, extracts frames (scene-aware, or fast keyframes at `efficient` detail), pulls a timestamped transcript (free captions when available, Whisper API as fallback), and `Read`s every frame as an image. By the time it answers, it has *seen* the video and *heard* the audio.

```
/watch https://youtu.be/dQw4w9WgXcQ what happens at the 30 second mark?
```

## What people actually use it for

**Analyze someone else's content.** `/watch https://youtu.be/<viral-video> what hook did they open with?` Claude looks at the first frames, reads the opening transcript, breaks down the structure. Same for ad creative, competitor launches, podcast intros, anything where the *how* matters as much as the *what*.

**Diagnose a bug from a video.** Someone sends you a screen recording of something broken. `/watch bug-repro.mov what's going wrong?` Claude watches the recording, finds the frame where the issue appears, describes what's on screen, often catches the cause without you ever opening the file.

**Summarize a video.** `/watch https://youtu.be/<long-thing> summarize this` does the obvious thing — pulls the structure, the key moments, what was actually said and shown. Faster than watching at 2x.

**Cut the hype out of an update video.** `/watch https://youtu.be/<launch-video> what's actually new — skip the hype` Strip a "game-changer" feature drop down to the few things that matter, so you get the substance without ten minutes of intro and overselling.

**Turn a playlist into notes.** `/watch https://youtu.be/<video> summarize this to a note` Run it across a series and file a per-video summary, so a channel or course becomes a searchable set of notes instead of hours you have to sit through.

## How it works

1. **You paste a video and a question.** URL (anything yt-dlp supports — YouTube, Loom, TikTok, X, Instagram, plus a few hundred more) or a local path (`.mp4`, `.mov`, `.mkv`, `.webm`).
2. **`yt-dlp` checks captions first.** At `transcript` detail, captioned URLs return without downloading video. Otherwise, or when Whisper needs audio, it downloads only what the run needs.
3. **`ffmpeg` extracts frames at the chosen detail.** `efficient` decodes keyframes only (near-instant); `balanced`/`token-burner` prefer scene-change frames and fall back to the duration-aware uniform sampler when they under-produce. JPEGs are 512px wide by default and clamped to 1998px tall for Claude Read compatibility.
4. **The transcript comes from one of two places.** First try: `yt-dlp` pulls native captions (manual or auto-generated) from the source. Free, instant, accurate-ish. Fallback: extract a mono 16 kHz 64 kbps mp3 audio clip (~480 kB/min) and ship it to Whisper — Groq's `whisper-large-v3` (preferred — cheaper and faster) or OpenAI's `whisper-1`.
5. **Frames + transcript are handed to Claude.** The script prints frame paths with `t=MM:SS` markers and the transcript with timestamps. Claude `Read`s each frame in parallel — JPEGs render directly as images in its context.
6. **Claude answers grounded in what's actually on screen and in the audio.** Not "based on the description" or "according to the title." It saw the frames. It heard the transcript. It answers the way someone who watched the video would.
7. **Cleanup.** The script prints a working directory at the end. If you're not asking follow-ups, Claude removes it.

## Frame budget — why it matters

Token cost is dominated by frames. Every frame is an image; image tokens add up fast. The script's auto-fps logic exists so you don't blow your context budget on a sparse scan of a 30-minute video that would have been better answered by a focused 30-second window.

| Duration | Default frame budget | What you get |
|----------|---------------------|--------------|
| ≤30 s | ~30 frames | Dense — basically every key moment |
| 30 s - 1 min | ~40 frames | Still dense |
| 1 - 3 min | ~60 frames | Comfortable |
| 3 - 10 min | ~80 frames | Sparse but workable |
| > 10 min | 100 frames (capped modes) | "Sparse scan" warning — re-run focused, or `--detail token-burner` for full uncapped coverage |

When the user names a moment ("around 2:30", "the last 30 seconds", "from 0:45 to 1:00"), pass `--start` / `--end`. Focused mode gets denser per-second budgets, capped at 2 fps. Far more useful than a sparse pass over the whole thing.

## Frame deduplication

Frame selection — keyframes (`efficient`), scene-change detection (`balanced`/`token-burner`), or the uniform sampler it falls back to — can still surface near-identical frames: a screen recording that holds one slide for 90 seconds produces a dozen, each billed as a separate image. A dedup pass drops them before frames reach Claude. It runs by default on every frame mode (`--no-dedup` turns it off):

1. One `ffmpeg` call scales each extracted JPEG to a 16×16 grayscale thumbnail. Everything after is pure-stdlib Python — no image libraries.
2. For each frame, compute the **mean absolute difference** against the *last frame that was kept* (average per-pixel brightness change, 0–255 scale).
3. If that difference is at or below the threshold (`2.0`), the frame is a near-duplicate and is dropped. Otherwise it's kept and becomes the new reference.
4. The frame-budget cap applies *after* dedup, so the budget is spent on distinct frames.

Comparing against the last *kept* frame (not the previous one) catches slow fades that never trip a frame-to-frame threshold. The threshold is deliberately low and measures absolute brightness rather than structure, so a one-line code diff, a terminal scrolling a row, or two differently-colored flat slides all survive.

The **Frames** line reports what was collapsed, e.g. `6 selected from 14 candidates (… 8 near-duplicates dropped …)`. On always-moving footage nothing is dropped and you pay what you would have anyway.

## Detail modes — measured

The `--detail` dial trades speed and token cost for visual fidelity. Numbers below are from a real run against a **49:08** YouTube video (1280×720, English auto-captions) — a long, mostly-static screen recording, the case that stresses the caps hardest. Extraction times are local CPU against a pre-downloaded copy; the one-time download was **~37 s** / 76 MB, shared by the three frame modes.

| Mode | Engine | Frames | Cap | Extraction time | Temporal coverage | Est. image tokens |
|------|--------|--------|-----|-----------------|-------------------|-------------------|
| `transcript` | none (captions) | 0 | — | **~4.5 s** (one yt-dlp call, no download) | full (text) | 0 (≈26.6k text tokens) |
| `efficient` | keyframe (`-skip_frame nokey`) | 50 | 50 | **~0.5 s** | 0:00 → 49:04 (full) | **~9.8k** |
| `balanced` | scene-change | 100 | 100 | **~20.9 s** | 0:00 → 48:38 (full) | **~19.7k** |
| `token-burner` | scene-change | 116 | uncapped | **~21.0 s** | 0:00 → 48:38 (full) | **~22.8k** |

- **Image tokens** use Anthropic's `(width × height) / 750` — at the default 512px width these 720p frames are 512×288, **≈197 tokens/frame**; `--resolution 1024` roughly 4×s that. The transcript is surfaced in every captioned mode and on long videos is often the larger cost.
- **One sampling rule across frame modes.** Each detects all candidates across the full range, then even-samples (first + last always kept) down to its cap. The modes differ only in candidate *source* (keyframes vs. scene cuts) and cap, never in how coverage is spread — so the last frame always lands at the end, not partway through.
- **`efficient` is the speed tier** (~0.5 s) — it only reconstructs keyframes, so it's ~40× faster than the scene modes, which decode every frame to find cuts. It can also return *more* frames than `balanced` on low-motion footage (keyframes outnumber scene cuts); "efficient" means fast extraction, not fewer frames.
- **`token-burner` only diverges from `balanced` past the cap.** This clip had 116 cuts, so `balanced` sampled 100 and `token-burner` kept all 116. On high-motion video with hundreds of cuts, `token-burner` keeps everything (and trips the >250-frame token warning) while `balanced` thins to 100.

End-to-end from a cold URL, `transcript` is the cheapest mode by far; the frame modes add the shared ~37 s download on top of the extraction times above.

## Install

| Surface | Install |
|---------|---------|
| **Claude Code** | `/plugin marketplace add bradautomates/claude-video` then `/plugin install watch@claude-video` |
| **Codex, Cursor, Copilot, Gemini CLI, +50 more** | `npx skills add bradautomates/claude-video -g` |
| **claude.ai** (web) | [Download `watch.skill`](https://github.com/bradautomates/claude-video/releases/latest) → Settings → Capabilities → Skills → `+` |
| **Manual / dev** | `git clone` then symlink `skills/watch` into your host's skills dir (see below) |

### Claude Code

```
/plugin marketplace add bradautomates/claude-video
/plugin install watch@claude-video
```

Update later with `/plugin update watch@claude-video`.

### Codex, Cursor, Copilot, Gemini CLI, and 50+ other hosts

The [Agent Skills](https://agentskills.io) CLI installs the skill into whatever agents it detects:

```bash
npx skills add bradautomates/claude-video -g
```

`-g` installs globally for your user (`~/.codex/skills`, `~/.cursor/skills`, etc.); drop it to install into the current project instead. Useful flags:

- `-a, --agent <names…>` — target specific hosts, e.g. `-a codex -a cursor`
- `-l, --list` — list the skills in this repo without installing
- `--copy` — copy files instead of symlinking (for filesystems without symlink support)

The CLI discovers the skill from `skills/watch/SKILL.md` and copies the whole folder — `SKILL.md` plus its `scripts/` runtime — as a self-contained unit. `SKILL.md` resolves its own scripts relative to wherever it was installed, so it works the same on every host.

Update later with `npx skills update watch -g`.

### claude.ai (web)

1. [Download `watch.skill`](https://github.com/bradautomates/claude-video/releases/latest) from the latest release.
2. Go to Settings → Capabilities → Skills.
3. Click `+` and drop the file in.

Enable "Code execution and file creation" under Capabilities first — the skill shells out to `ffmpeg` and `yt-dlp`, so it won't run without it.

### Manual (developer)

Clone the repo and symlink the self-contained skill folder into your host's skills directory — the symlink keeps the install in sync with your working tree as you edit:

```bash
git clone https://github.com/bradautomates/claude-video.git
ln -s "$(pwd)/claude-video/skills/watch" ~/.claude/skills/watch   # or ~/.codex/skills/watch
```

For claude.ai, build the `.skill` bundle from source: `bash skills/watch/scripts/build-skill.sh` produces `dist/watch.skill`.

## First run

On the first `/watch` call, the skill runs `scripts/setup.py --check`. If `ffmpeg` / `yt-dlp` aren't on your PATH, or no Whisper API key is set, it walks you through fixing it:

- **macOS** — auto-runs `brew install ffmpeg yt-dlp`.
- **Linux** — prints the exact `apt` / `dnf` / `pipx` commands.
- **Windows** — prints the `winget` / `pip` commands.
- **API key** — scaffolds `~/.config/watch/.env` (mode `0600`) with commented placeholders for `GROQ_API_KEY` (preferred) and `OPENAI_API_KEY`.

After setup, preflight is silent and `/watch` just works. The check is a sub-100ms lookup, so it doesn't slow you down on subsequent runs.

## Bring your own keys

Captions cover the majority of public videos for free. The Whisper fallback only kicks in when a video genuinely has no caption track — typically local files, TikToks, some Vimeos, and the occasional caption-less YouTube upload.

| Capability | What you need | Cost |
|------------|---------------|------|
| Download + native captions | `yt-dlp` + `ffmpeg` | Free |
| Whisper fallback (preferred) | [Groq API key](https://console.groq.com/keys) — `whisper-large-v3` | Cheap, fast |
| Whisper fallback (alt) | [OpenAI API key](https://platform.openai.com/api-keys) — `whisper-1` | Standard pricing |
| Disable Whisper entirely | `--no-whisper` | Free, frames-only when no captions |

## Usage

```
/watch https://youtu.be/dQw4w9WgXcQ what happens at the 30 second mark?
/watch https://www.tiktok.com/@user/video/123 summarize this
/watch ~/Movies/screen-recording.mp4 when does the UI break?
/watch https://vimeo.com/123 what tools does she mention?
```

Focused on a specific section — denser frame budget, lower token cost:
```
/watch https://youtu.be/abc --start 2:15 --end 2:45
/watch video.mp4 --start 50 --end 60
/watch "$URL" --start 1:12:00            # from 1h12m to end
```

Other knobs (passed to `scripts/watch.py`):

- `--detail transcript|efficient|balanced|token-burner` — fidelity/speed dial. `transcript` skips frames (transcript only); `efficient` uses fast keyframes (cap 50); `balanced` uses scene-aware frames (cap 100); `token-burner` is scene-aware and uncapped.
- `--timestamps T1,T2,…` — grab a frame at each absolute timestamp (`SS`/`MM:SS`/`HH:MM:SS`). Claude reads the transcript first, then targets the moments the presenter flags ("look here", "as you can see"). Added on top of the detail frames (reserved against the cap); out-of-window cues are dropped in focus mode; with `--detail transcript` these become the only frames.
- `--max-frames N` — lower the frame cap for a tighter token budget.
- `--resolution W` — bump frame width to 1024 px when Claude needs to read on-screen text (slides, terminals, code).
- `--fps F` — override the auto-fps calculation (still capped at 2 fps).
- `--whisper groq|openai` — force a specific Whisper backend.
- `--no-whisper` — disable transcription entirely; frames only.
- `--no-dedup` — keep near-duplicate frames. By default a frame-delta pass drops frames that are visually near-identical to the one before them (held slides, static screen recordings, paused video), so the frame budget is spent on distinct content; this flag turns that off.
- `--out-dir DIR` — keep working files somewhere specific (default: auto-generated tmp dir).

## Limits

- **Long-video accuracy depends on the detail mode.** On the capped modes (`efficient`, default `balanced`) coverage thins out past ~10 minutes — the frame cap spreads across the whole clip, so the script prints a "sparse scan" warning and you're better off re-running focused with `--start`/`--end`. `token-burner` lifts the cap and keeps *every* scene-change frame across the full video, so it stays complete on longer clips at the cost of more image tokens. The 10-minute mark is guidance for the capped modes, not a hard ceiling.
- **Detail is one dial.** Defaults are balanced: scene-aware frames, 2 fps max, 100-frame cap. Use `--detail efficient` for a fast 50-frame keyframe pass, or `--detail token-burner` for uncapped scene candidates. Set `WATCH_DETAIL` in `~/.config/watch/.env` to change the default.

## Structure

```
.
├── skills/watch/                 # self-contained skill — copied as a unit by every installer
│   ├── SKILL.md                  # skill contract — the source of truth across all surfaces
│   └── scripts/
│       ├── watch.py              # entry point — orchestrates download → frames → transcript
│       ├── download.py           # yt-dlp wrapper
│       ├── frames.py             # ffmpeg frame extraction + auto-fps logic
│       ├── transcribe.py         # VTT parsing + dedupe + Whisper orchestration
│       ├── whisper.py            # Groq / OpenAI clients (pure stdlib)
│       ├── config.py             # shared config (~/.config/watch/.env)
│       ├── setup.py              # preflight + installer
│       └── build-skill.sh        # build dist/watch.skill for claude.ai upload (dev-only)
├── hooks/                        # SessionStart status hook (Claude Code only)
├── .claude-plugin/               # plugin.json + marketplace.json (Claude Code)
├── .codex-plugin/                # plugin.json — Codex/agents manifest ("skills": "./skills/")
├── .agents/plugins/              # marketplace.json — Agent Skills marketplace listing
├── AGENTS.md → CLAUDE.md         # generic-agent entry point
├── tests/                        # pytest suite (ffmpeg-synthesized clips, no network)
└── .github/workflows/            # release.yml — auto-builds watch.skill on tag push
```

## Develop

```bash
# Run the test suite (stdlib + pytest; ffmpeg required for frame tests):
python3 -m pytest -q

# Build the claude.ai upload bundle:
bash skills/watch/scripts/build-skill.sh      # → dist/watch.skill
```

Releasing: tag `vX.Y.Z`, push the tag. The workflow builds `dist/watch.skill` and attaches it to the GitHub release. Keep the version in sync across `skills/watch/SKILL.md`, `.claude-plugin/plugin.json`, and `.codex-plugin/plugin.json`.

See [CHANGELOG.md](CHANGELOG.md) for version history.

## Open source

MIT license.

Built on `yt-dlp`, `ffmpeg`, and Claude's multimodal `Read` tool. Whisper transcription via [Groq](https://groq.com) or [OpenAI](https://openai.com).

Built by Brad Bonanno — I make content about building with AI on [YouTube (@bradbonanno)](https://www.youtube.com/@bradbonanno), and build AI operating systems for businesses at [Solaris Automation](https://www.solarisautomation.io/). If `/watch` saves you from scrubbing through a video, come say hi on the channel.

## Star History

<a href="https://www.star-history.com/?repos=bradautomates%2Fclaude-video&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=bradautomates/claude-video&type=date&theme=dark&legend=top-left" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=bradautomates/claude-video&type=date&legend=top-left" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=bradautomates/claude-video&type=date&legend=top-left" />
 </picture>
</a>

---

[github.com/bradautomates/claude-video](https://github.com/bradautomates/claude-video) · [@bradbonanno](https://www.youtube.com/@bradbonanno) · [Solaris Automation](https://www.solarisautomation.io/) · [LICENSE](LICENSE)


## Video Essay Analyzer

This fork adds an end-to-end analyzer that accepts a **YouTube/video URL or local video file** and an optional transcript.

Supported input combinations:

- YouTube URL
- YouTube URL + transcript
- Local MP4/video file
- Local MP4/video file + transcript
- Transcript only: **not supported** (visual evidence is required)

The transcript may be either a UTF-8 text file path or literal text. When no transcript is supplied, the analyzer tries native/auto captions first (Japanese, then English by default), then falls back to Groq/OpenAI Whisper when an API key is configured.

### Run

```bash
python3 skills/watch/scripts/analyze.py "https://www.youtube.com/watch?v=..."
python3 skills/watch/scripts/analyze.py "https://www.youtube.com/watch?v=..." --transcript transcript.txt
python3 skills/watch/scripts/analyze.py "/path/to/video.mp4"
python3 skills/watch/scripts/analyze.py "/path/to/video.mp4" --transcript transcript.txt
```

Windows:

```powershell
python skills/watch/scripts/analyze.py "C:\\path\\to\\video.mp4"
```

Set `OPENAI_API_KEY` in the environment or in `~/.config/watch/.env`. The analyzer uses the OpenAI Responses API with structured JSON output. The default analysis model is `gpt-5.6` and can be changed with `--model`.

Useful options:

```text
--transcript PATH_OR_TEXT
--sub-langs ja.*,en.*
--language ja
--model gpt-5.6
--max-frames 40
--resolution 512
--out analysis.json
--db ~/.config/watch/video_essay_analyzer.db
--no-save
--work-dir DIR
```

### Outputs

Each run produces a structured JSON analysis and, unless `--no-save` is used, stores the record in SQLite.

The analysis schema covers:

- title / thumbnail packaging
- first-30-second hook
- chapter and structural progression
- argumentation and evidence
- storytelling and emotional arc
- visual grammar and editing rhythm
- retention hypotheses and drop-off risks
- likely CTR / watch-time drivers (explicitly labeled as hypotheses)
- reusable patterns
- prioritized improvements
- timestamped evidence
- confidence and limitations

SQLite default location:

```text
~/.config/watch/video_essay_analyzer.db
```

The original `/watch` skill remains available and unchanged in purpose; `analyze.py` is the persistent, structured Video Essay Analyzer workflow.

## Video Essay Analyzer v2 (research pipeline)

v2 adds a normalized research database and millisecond timeline while preserving `/watch` and the original `skills/watch/scripts/analyze.py`. Start the new pipeline from the repository root:

```bash
python analyze.py "https://www.youtube.com/watch?v=VIDEO_ID" --mode standard
python analyze.py video.mp4 --transcript transcript.txt
python analyze.py sample.mp4 --transcript transcript.vtt --title "Original public title" --excerpt
python analyze.py video.mp4 --mode fast
python analyze.py video.mp4 --mode deep --deep-config deep-models.json
```

### 初めて使う場合

Python 3.10以上、FFmpeg（ffprobeを含む）、yt-dlpを準備します。WindowsではPythonとFFmpegの実行ファイルをPATHに追加してください。yt-dlpは `python -m pip install yt-dlp` で導入できます。`ffmpeg -version`、`ffprobe -version`、`yt-dlp --version` で確認します。

OpenAIの分析を使う場合は、PowerShellで `$env:OPENAI_API_KEY="自分のキー"` を設定します。YouTubeの正確な公開日時・比較対象一覧の取得には `$env:YOUTUBE_API_KEY="自分のキー"` を設定します。キーはGitHubやチャットに貼り付けません。OpenAIの既存設定ファイル `~/.config/watch/.env` も利用できます。`.env` を自動生成・コミットする処理はありません。

APIなしでローカルの動作確認をするには:

```bash
python analyze.py video.mp4 --transcript transcript.txt --offline --db analysis-output/research.db --out analysis-output/result.json
```

`--offline` はAPI呼び出しを止めます。映像からのショット・色・無音・音量分析は動作し、LLMによる解釈は未実行として記録されます。トランスクリプトだけでは起動できません。指定した字幕を優先し、未指定なら日本語→英語の字幕→Whisperの順に試します。`--sub-langs "de.*,en.*"` で変更できます。時刻のない文章に時刻を捏造しません。

### モードと現在の実装範囲

| モード | 実装 | 条件・制限 |
|---|---|---|
| Fast | 入力、字幕、代表フレーム、OpenAI構造化分析、タイムライン、DB | 意味分析にはOpenAIキーが必要 |
| Standard（既定） | Fast＋FFmpegショット検出・編集指標・ショット単位の色・無音・LUFS、任意CLIP/PANNs | CLIP/PANNsは別途パッケージとローカルモデルが必要。PANNsはspeech/music、SFXは未実装 |
| Deep | Standard＋librosaビート、任意WhisperX単語整列・TransNetV2・Demucs接続 | 重いモデルの実機統合検証は未実施。Florence-2・B-rollとナレーションの意味類似度は未実装 |

v2では代表フレーム予算の約半分（最低4枚、総予算内）を動画全体の等分区間の中央に配置し、残りをシーン変化候補に割り当てます。カットの少ないアニメーションでも中盤・終盤を見逃しにくくします。ショット検出は閾値による推定のままで、連続した図の変化をすべてカットとして数えません。

OpenAIは根拠付きの主張・章・Hook・映像素材・A/B-roll判断・タイトル/サムネイル/内容整合性の評価を返します。代表フレームの判断から動画全体の素材比率を捏造しません。CLIPで全ショットの中間フレームを分類できた場合のみ、素材比率を「ショット中間フレームから推定したモデル由来値」として保存します。

### データの場所・比較・削除

既定DBは `~/.config/watch/video_essay_analyzer.db`（Windowsではユーザーホーム以下）です。`--db` で変更できます。旧 `analyses` テーブルは保持され、v2が `videos`、`analysis_runs`、`performance_snapshots`、`timeline_events`、`video_features` などを追加します。旧履歴は自動移行しません。同一YouTube IDや同じ内容のローカル動画は1動画として扱い、再分析すると分析履歴とスナップショットを追加します。

```bash
python analyze.py --list
python analyze.py --snapshot yt:VIDEO_ID
python analyze.py --export csv --out research.csv
python analyze.py --export json --out research.json
python analyze.py --delete yt:VIDEO_ID --yes
python analyze.py --batch videos.txt --mode standard
```

`videos.txt` は1行1動画のUTF-8ファイルです。バッチの1動画が失敗しても次へ進みます。結果JSON・フレームは画面に出る `work_dir` に保存されます。`--work-dir analysis-output` で保存場所を固定できます。削除は対象動画のv2 DBデータを削除します。元動画・フレーム・書き出し済みファイル・旧履歴は削除しません。

CSVは1動画1行（最新分析＋最新パフォーマンス）で、特徴量の値・欠損理由・観測/モデル由来の区別・計算根拠を保持します。JSONにはイベントと分析根拠も含みます。モデルや取得時期が異なる値をそのままプールしないでください。分析対象の関連性は因果効果を意味しません。

### 相対パフォーマンス

対象再生数÷同一チャンネルで対象より前に公開された直近10本の再生数中央値。比較対象はYouTube Data APIのアップロード一覧から取得します。既定ではShortsを含めます。`--exclude-shorts` ではAPIだけでShortsを完全判別できないため、現在は正式な相対値を不明として扱います。DB内の「分析した直近10本」を正式な比較対象へ代用しません。

10本未満、欠損、中央値0、一覧の探索上限（20ページ）に達した場合は限定・不明として記録します。公開からの経過日数で割ったviews/dayは累積平均であり、直近1日の増加数ではありません。過去24時間・7日後の再生数は現在の値から復元しません。`--snapshot` で今後の観測を蓄積できます。

### 任意モデルの設定

CLIP: `--clip-model PATH_TO_LOCAL_HF_CLIP`。`torch`、`transformers`、`Pillow`が必要です。PANNs: `--audio-checkpoint PATH_TO_CNN14_CHECKPOINT`。`panns-inference`、`librosa`等が必要です。パッケージ・weightsの対応バージョンをローカル環境で検証してから利用してください。自動インストールはしません。

Deep設定ファイル例（使う項目だけ記載）:

```json
{
  "device": "cpu",
  "whisperx_model": "C:/models/whisper",
  "alignment_model": "C:/models/alignment",
  "language": "ja",
  "transnet_module": "C:/models/TransNetV2/inference/transnetv2.py",
  "transnet_weights": "C:/models/TransNetV2/inference/transnetv2-weights",
  "demucs_repo": "C:/models/demucs",
  "demucs_model": "htdemucs"
}
```

ユーザー字幕がある場合、DeepでもWhisperXで置き換えません。Demucsの非ボーカル音声には効果音も含まれうるため、その存在だけで音楽と判定しません。ビート同期率は音楽分類が成功した区間のみで計算します。

詳細: [仕様](docs/v2-spec.md)、[構成](docs/architecture.md)、[DB](docs/data-model.md)、[Timeline](docs/timeline-schema.md)、[数式とモード](docs/analysis-modes.md)、[依存とライセンス](docs/dependencies.md)、[検証と未完了事項](docs/validation.md)。


### 結果をブラウザーで読む（API再実行不要）

`skills/watch/result-viewer.html` をダブルクリックし、既存の `result.json` を選択してください。画面には入力資料で確認できる情報、AIの解釈、未確認・不足情報を分けて表示します。複数JSONを選ぶと動画を切り替えられます。ファイルはブラウザー内で処理し、通信・API呼び出し・保存は行いません。従来のJSONにも対応し、既存分析の内容を再判定したり書き換えたりしません。

今後の分析では告知を無料登録・有料購入・チャンネル登録などに分け、無料／有料が不明なら未確認とします。新しい分類は既存JSONへ自動追加しません。画像取得は区間中央のアンカーを優先し、取得失敗数と最大の未撮影時間も記録します。WindowsのFFmpeg出力はUTF-8で読み取り、API応答の待ち時間は600秒、自動再試行なしです。

### 声・音楽と画面切替の時間軸（短区間の試作）

結果画面の「音声・映像」で、声と音楽の推定区間を別々の列に、既存のシーン変化を同じ時間軸に表示します。元MP4を選べば色の区間をクリックして再生位置を移動できます。ブラウザー内の再生のみで、動画をアップロードしません。

任意の追加処理（Python 3.12 / CPUで検証済み。Python 3.14の依存パッケージ対応は未検証）：

```sh
python -m pip install numpy onnxruntime
python skills/watch/scripts/vea/audio_timeline.py video.mp4 --result analysis-output/result.json --model /path/to/yamnet.onnx --labels /path/to/yamnet_class_map.csv --seconds 60 --out analysis-output/audio-result.json
```

モデルは別途ローカルに配置します。Google YAMNetの[ONNX変換版](https://huggingface.co/audiomagic/yamnet-onnx/tree/f25b741c2f0bdc6d7e6db24b5fddda23347dbafd)の `yamnet.onnx` と `yamnet_class_map.csv` を使用して検証しました。モデルはApache-2.0、AudioSetラベルはCC BY 4.0。重みはこのリポジトリに同梱せず、自動ダウンロードも行いません。[Google公式説明](https://www.tensorflow.org/hub/tutorials/yamnet)に従い16kHzモノラル音声を使い、モデルとラベルのハッシュを結果に記録します。

解析は0.96秒の窓／0.48秒刻み。声と音楽の重複を許容します。音楽はBGM・劇中音楽などを区別できず、声はナレーター以外も含みます。未検出は無音や不存在を意味しません。境界・スコアは推定であり、音楽の入り方や抑揚の質を評価する機能ではありません。しきい値は0.3が試作の既定値で、実動画での精度は聴取照合が必要です。

既存のJSONを入力し、`media_timeline` を追加した別ファイルを作ります。元のAI分析、全編の特徴量とrun_idは保持し、追加音声解析には独自の解析時刻と対象区間を記録します。動画ハッシュが一致しない場合は拒否し、元JSONへの上書きも拒否します。`--start` と `--seconds` で対象区間を選べます。結果画面では未解析の全編を分析済みと表示しません。
