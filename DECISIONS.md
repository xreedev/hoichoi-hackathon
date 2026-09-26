# Decisions log

One line per decision. **(you)** = your call, **(me)** = my call under "make the simplest reasonable call".

## Step 0 / Milestone A
- (you) Keys: Gemini and Sarvam are in `.env`. TypeSafe comes later; decisions run on the Gemini fallback until then.
- (you) Pacing: §5 defaults (6 breaks/h, 420 s gap, 15% ad load, 15 s ads). No handbook values were supplied.
- (you) Manifest: VMAP 1.0.1 wrapping inline VAST 4.2.
- (you) `Brand.description` is set to the catalogue's `category`.
- (you) Creative = each brand's shortest one. No ad files were supplied, so each is a generated slate of that length.
- (you) Keyword check: block a brand if one of its negative contexts appears in the previous or next scene's activity or setting.
- (you) Synonyms: exact matches only (`config/synonyms.yaml` is empty).
- (you) Develop on `bhojon_bilashi`; `mohanagar` is held out for the final live test.
- (you) M5 prompt: split scenes at activity/location changes; scenes typically 30 s – 3 min.
- (you) Break cap: at most 6 breaks in any rolling 60-min window.
- (you) Defaults: client timeouts, and a 3 s "full pause" for scoring.
- (me) Added `python-multipart`, which FastAPI needs for uploads.
- (me) Added `paths:`, `timeouts:` and `scoring.pause_len_cap_s` to config; the spec gave no values.
- (me) M5 returns shot-id ranges, which are repaired so scenes always tile the whole video.
- (me) M10 re-checks ad load with the real creative lengths and drops the lowest-scoring break if it's over the cap.
- (me) One shared Gemini SDK client per process; creating clients concurrently was closing requests mid-call.

## After Milestone A
- (you) Breaks are allowed only at scene boundaries. The sensitivity check covers the scenes before and after the cut; the cliffhanger rule covers the scene before.
- (me) M6 maps a boundary cut to the scenes on either side of the nearest boundary, even when the cut is a few frames off it.
- (you) Scope: skip M4 (placeholder stays) and delete M7. M8 stays a placeholder until deploy works and a TypeSafe key arrives.
- (me) Removed `use_vjepa` and the V-JEPA model id from config. `visual_change` stays in the schema with weight 0.
- (me) Result on the dev sample: only 3 of 12 scene boundaries fall in silence, and the 420 s gap leaves room for 1 break. Accepted as is.
- (me) API keeps runs in memory; the pipeline runs in a background thread; SSE streams the run's event list. Re-match considers every brand in the current catalogue.
- (me) Add brand / upload edits a working copy of the catalogue (`runs/_catalogue.json`); `assets/brands.json` is never modified.
- (me) The UI is one static page, vanilla JS. The player reads our VMAP and plays the ad in a second <video> over the content, then seeks back to the exact offset (tested: +0.13 s).
- (me) Docker: `python:3.11-slim` + ffmpeg + DejaVu font (for slate text). CPU torch is installed first from the PyTorch index; the app runs as uid 1000 on port 7860.
- (me) Deploy goes through a GitHub Action (`.github/workflows/deploy-hf.yml`, which runs `scripts/deploy_hf.py`) using the `HF_TOKEN` / `GEMINI_API_KEY` / `SARVAM_API_KEY` repo secrets, so no token passes through chat. The Space gets code, catalogue and the dev sample only (never `mohanagar`).
- (me) No baked cache: the Space's first run of a video is cold (a few minutes); repeat runs and re-match use the cache until the Space restarts.
