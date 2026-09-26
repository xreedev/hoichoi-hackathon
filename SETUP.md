# Run it locally

About 10 minutes to set up. The first run of a 20-minute episode takes about 4 minutes; after that it's cached.

## 1. Prerequisites

- **Python 3.11**
- **ffmpeg** (includes `ffprobe`):
  - macOS: `brew install ffmpeg`
  - Ubuntu/Debian: `sudo apt install ffmpeg`
  - Windows: `winget install ffmpeg`
- A **Gemini API key** from https://aistudio.google.com/apikey
- Optional: a Sarvam key (not used yet) and a TypeSafe key. Without TypeSafe, decisions use Gemini.
- A normal browser (Chrome, Edge, Firefox or Safari) for the UI.

## 2. Install

```bash
git clone https://github.com/xreedev/hoichoi-hackathon.git
cd hoichoi-hackathon
git checkout claude/adoring-pasteur-w30gy0

python3.11 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate

pip install torch --index-url https://download.pytorch.org/whl/cpu   # CPU-only torch, ~200 MB
pip install -r requirements.txt
```

## 3. Add your keys

```bash
cp .env.example .env
```

Open `.env` and fill in the keys. `.env` is git-ignored.

```
GEMINI_API_KEY=your-gemini-key
SARVAM_API_KEY=
TYPESAFE_API_KEY=
```

## 4. Get the sample videos

The brand catalogue is already at `assets/brands.json`. The videos are too large for git, so download them into `assets/`:

```bash
pip install gdown
gdown --folder https://drive.google.com/drive/folders/1mmirzihj-SxaTTdH2248BrMKqBphZYEB -O assets
```

Any `.mp4` placed in `assets/` shows up in the UI's sample list. You can also upload a video from the UI.

## 5. Check everything works

```bash
python -m app.cli health
```

Expected: `ffmpeg`, `ffprobe` and `gemini` show **OK**. Keys you didn't set show **SKIPPED**, which is fine.

## 6. Run the demo UI

```bash
uvicorn app.api:app --port 7860
```

Open **http://localhost:7860**, then:

1. **Pick a sample** (or upload a video) and click **Run**. Progress streams in on the left.
2. When it's done, press play. At each break the video **cuts to the brand slate ad**, then **resumes** where it left off.
3. The **timeline** shows scenes, speech and candidate cuts:
   - Scene colour is the mood; red hatching marks a sensitive scene.
   - Green cuts are ad breaks; grey cuts were rejected.
4. **Click a candidate** to see why it was chosen or rejected, the brand ranking, and which brands were **blocked**, with the rule and evidence.
5. **Add brand** (left panel) → **Re-match brands**. This re-runs only pacing, brands and manifest from the cache, so it takes seconds.
6. **Download VMAP** / **Download debug.json** give you the manifest and the full audit trail.

## 7. Command line (no UI)

```bash
python -m app.cli run --video assets/bhojon_bilashi.mp4      # full pipeline → prints the breaks
python -m app.cli scenes --video assets/bhojon_bilashi.mp4   # scene table only
python -m app.cli candidates --video assets/bhojon_bilashi.mp4
```

Outputs go to `runs/<video sha256>/`: `vmap.xml`, `debug.json`, one JSON file per stage, and the 360p proxy. Add `--force` to recompute instead of using the cache.

## 8. Tests

```bash
ruff check .
pytest -m "not live"      # offline, about 30 s: synthetic media + recorded/mocked AI responses
pytest -m live -s         # calls the real APIs; needs the sample videos
```

## Alternative: Docker

```bash
docker build -t hoichoi-adbreaks .
docker run --rm -p 7860:7860 --env-file .env -v "$PWD/assets:/home/user/app/assets:ro" hoichoi-adbreaks
```

Then open http://localhost:7860. The image includes `ffmpeg` and everything else; the `-v` flag makes every video in `assets/` available as a sample.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `ffmpeg not on PATH` in `health` | Install ffmpeg (step 1) and reopen the terminal. |
| `gemini FAIL ... 429 / RESOURCE_EXHAUSTED` | Free-tier rate limit. Calls retry with backoff; wait a minute and re-run. |
| A run is slow the first time | Normal. Ingest and Gemini scene analysis take about 4 min for a 20-min episode; later runs use the cache in `runs/`. |
| Video doesn't play in the browser | Use Chrome, Edge, Firefox or Safari. Some stripped-down Chromium builds lack H.264. |
| Want a clean slate | Delete `runs/<sha>/` for that video, or run with `--force`. |
| Brand edits in the UI | They go to `runs/_catalogue.json`. Delete that file to go back to `assets/brands.json`. |
