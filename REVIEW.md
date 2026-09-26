# Progress Review — Context-Aware Ad Placement

Branch: `claude/adoring-pasteur-w30gy0` · Last commit: `222e4d5` (Milestone A)

## 1. What's built

| Module | Status | Notes |
|---|---|---|
| M0 health | ✅ | ffmpeg, ffprobe, Gemini and Sarvam are OK; TypeSafe is SKIPPED (no key yet) |
| M1 ingest | ✅ | sha256, ffprobe, 360p proxy, 16 kHz mono WAV |
| M2 shots | ✅ | PySceneDetect AdaptiveDetector |
| M3 vad | ✅ | Silero VAD |
| M4 asr | ⏳ placeholder | returns an empty transcript and logs a warning |
| M5 scenes | ✅ | Gemini 3.8 Flash with agentic video; confirmed running |
| M6 candidates | ✅ | a cut qualifies only inside silence, never mid-speech |
| M7 V-JEPA | ⏸ optional | not started |
| M8 decide | ⏳ placeholder | passes candidates through unchanged and logs a warning |
| M9 pacing | ✅ | safety gates, scoring, greedy selection |
| M10 brands | ✅ | tag filter, keyword filter, AI filter, AI ranking, reason sentence |
| M11 manifest | ✅ | VMAP 1.0.1 + inline VAST 4.2, slate videos, debug.json |
| M12 API | 🟡 partial | pipeline runner and CLI done; FastAPI endpoints not built |
| M13 UI | ❌ | not started |
| M14 eval | ❌ | not started |

**Milestone A is reached:** `python -m app.cli run --video assets/bhojon_bilashi.mp4` → `vmap.xml` + `debug.json`.

Tests: 36 pass offline, and ruff is clean.

## 2. Decisions taken

### From you (Step 0)
| # | Decision |
|---|---|
| Keys | Gemini and Sarvam keys are in `.env` (git-ignored). The TypeSafe key comes later; until then Jev runs on the Gemini fallback. |
| Pacing | §5 defaults: 6 breaks/h, 420 s minimum gap, 15% ad load, 15 s ads |
| Manifest | VMAP 1.0.1 wrapping inline VAST 4.2 |
| Brand.description | set to the catalogue's `category` |
| Creative | each brand's **shortest** creative. No ad files were supplied, so every creative is a generated slate of that length. |
| Keyword check | **added**: block a brand if one of its negative contexts appears as a word in the previous or next scene's activity or setting |
| Synonyms | **exact matches only**, so `config/synonyms.yaml` is empty |
| Videos | develop on `bhojon_bilashi`; `mohanagar` is held out for the final unseen-video test |
| Scene prompt | added generic guidance: split at activity/location changes, scenes typically 30 s – 3 min |
| Break cap | at most 6 breaks in any **rolling 60-min window** |
| Defaults | timeouts (Gemini 120 s text / 1200 s video, Sarvam 1800 s, Jev 30 s) and the 3 s "full pause" kept |

### Made by me (small, flagged)
- `python-multipart` added to requirements, because FastAPI needs it for video uploads.
- New `paths:` section in config for the runs dir, catalogue and synonyms files.
- `timeouts:` and `scoring.pause_len_cap_s` added to config; the spec gave no values.
- M5 asks Gemini for shot-id ranges rather than timestamps, then repairs the result so scenes always tile the full video.
- M10 re-checks ad load using real creative lengths and drops the lowest-scoring break if the cap is exceeded.
- Environment: ffmpeg installed; ruff line length set to 120.

## 3. Result on the dev sample (bhojon_bilashi, 20.5 min)

432 shots → 13 scenes → 83 candidates → 2 breaks:

| Break | Time | Brand | Why |
|---|---|---|---|
| break-1 | 06:18.8 | Brand G (travel), 20 s | at a scene boundary; the scenes are sightseeing at a heritage site |
| break-2 | 14:51.6 | Brand A (food), 15 s | inside the cooking scene, p = 0.94 |

Brand B (negative context `eating`) was blocked by the AI filter at both breaks.

Timing: the full perception pass takes about 3 min. A re-match that reuses cached perception takes about 15 s.

## 4. Open questions (not yet answered)

1. **Safety zone:** should the sensitivity checks also look at scenes within ±30 s of a cut? Today a cut 2 s after a sensitive scene only checks the scene it sits in.
2. **Cliffhanger rule:** today it rejects every cut inside a scene that ends on a cliffhanger. Should it apply only at the scene's end?

## 5. Remaining work (build order)

1. M8 decide: Jev questions, the ±20 s clip re-check when confidence is low, and the Gemini fallback
2. M4 asr: Sarvam batch, with Gemini Transcribe as fallback
3. M12 FastAPI endpoints and M13 UI with the ad-playing player → **Milestone B**
4. Docker image, Hugging Face Space deploy, run the held-out video on the live URL
5. M14 eval and README; M7 only if time allows
