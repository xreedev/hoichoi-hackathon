# BreakSense — Context-Aware Ad Breaks for Bengali Drama

BreakSense analyses a long-form OTT episode, finds every moment that is safe and natural for an ad break, and matches each break to the most relevant brand from a catalogue. It emits a **VMAP 1.0.1 / VAST 4.2** manifest that a video player can consume directly, along with a full-featured browser UI.

The core philosophy: **deterministic gates first → AI for judgment → hard rules last.** The AI never decides whether a break is safe; it only decides which brand fits best.

---

## How it works — end to end

```
Video file
   │
   ├─ M1  Ingest ──────── SHA-256 fingerprint, 360p proxy, 16 kHz mono WAV
   │
   ├─ M2  Shots ────────── shot-cut timestamps (PySceneDetect AdaptiveDetector)
   ├─ M3  VAD ──────────── speech / silence timeline (Silero VAD)
   ├─ M4  ASR ──────────── transcript chunks with word-level timestamps (Sarvam Saaras / Gemini fallback)
   │
   ├─ Upload ───────────── proxy video uploaded to Gemini Files API (reused across stages)
   │
   ├─ M5  Scenes ───────── semantic scene segmentation (Gemini 3.8 Flash, agentic video analysis)
   │                        → mood, narrative tension, dominant activity, setting,
   │                          sensitive tags, ends-on-cliffhanger flag
   │
   ├─ M6  Candidates ───── intersect shot cuts with VAD silence windows
   │                        → keep only cuts with ≥ min_pause_s silence on each side,
   │                          outside the first/last 5% of runtime
   │
   ├─ M8  Decide ───────── per-candidate AI scoring (TypeSafe Jev / Gemini fallback)
   │                        → break_quality label, emotional_peak probability,
   │                          ends_scene probability, sensitive_context probabilities
   │
   ├─ M9  Pacing ───────── safety gates + weighted scoring + greedy selection
   │                        → hard-reject: sensitive scene (p > 0.35), emotional peak (p > 0.7)
   │                        → score: pause length, scene boundary, break quality, low tension
   │                        → select up to 20 breaks/hr, ≥ 30 s apart, ≤ 25% ad load
   │
   ├─ M10 Brands ───────── per-break brand matching
   │                        → Tag filter: brand negative_contexts vs sensitive tags (hard block)
   │                        → Keyword filter: negative_contexts vs scene activity/setting text
   │                        → Semantic filter: Noul question per brand over negative_contexts
   │                        → AI ranking: Choice question picks best-fit brand per break
   │                        → Diversity allocation: each brand gets its best unclaimed slot
   │                        → Reason: one-sentence English rationale per placement
   │
   └─ M11 Manifest ─────── VMAP 1.0.1 with inline VAST 4.2 per break, debug.json, vmap.xml
```

All upstream stages (ingest → scenes) are cached on disk keyed by video SHA-256 and config hash. Changing `config.yaml` only busts the stages downstream of the changed section. Pacing, brands and manifest always re-run from the cache (they're fast).

---

## Pipeline modules in detail

### M1 — Ingest (`app/modules/ingest.py`)
**What:** Creates the 360p proxy video and 16 kHz mono WAV for all downstream stages. Computes the SHA-256 of the source file which is used as the cache key for every other stage.

**Why:** Normalises codec, resolution and sample rate so later stages never deal with variable input formats. The SHA-256 guarantees deterministic caching — re-running the same episode never re-calls any paid API.

### M2 — Shots (`app/modules/shots.py`)
**What:** Runs PySceneDetect's `AdaptiveDetector` on the proxy to find every hard shot cut. Each shot boundary is a candidate start time.

**Why:** Ad breaks can only be placed at shot cuts — mid-shot breaks are visually jarring. Shot detection is purely local (no API cost) and runs in seconds.

### M3 — VAD (`app/modules/vad.py`)
**What:** Runs Silero VAD 6.2 on the 16 kHz WAV to produce a speech/silence timeline. Silence windows are gaps between speech segments with `min_silence_duration_ms: 100` minimum length.

**Why:** Breaking into speech is the #1 viewer complaint for ad insertion. VAD ensures candidates are gated to actual silent moments. Silero is a tiny local model — no API, runs on CPU in seconds.

### M4 — ASR (`app/modules/asr.py`)
**What:** Transcribes the audio using Sarvam `saaras:v3` (Bengali-specialist ASR) with Gemini transcription as fallback. Produces transcript chunks with timestamps.

**Why:** Transcript chunks are used as a secondary guard in candidate detection — a break cannot land inside a spoken phrase even if VAD shows silence (e.g., a brief breath in the middle of a sentence).

**Tools used:** Sarvam AI Saaras v3 (Bengali ASR), Gemini asr_fallback model.

### M5 — Scenes (`app/modules/scenes.py`)
**What:** Sends the proxy video to Gemini 3.8 Flash (agentic video understanding) and asks it to segment the episode into semantic scenes, labelling each with:
- `summary_en` — English plot summary
- `dominant_activity`, `setting`, `mood`
- `narrative_tension` (0–1 float)
- `sensitive_tags` — list of sensitive content flags (violence, grief, intimacy, etc.) with probabilities
- `ends_on_cliffhanger` — boolean

**Why:** Scene metadata powers both safety gating (sensitive scenes block adjacent breaks) and brand matching (target/negative contexts are matched against activity and setting). Without scene understanding, brand safety is guesswork.

**Tools used:** Gemini 3.8 Flash (agentic video, `gemini-3.8-flash`).

### M6 — Candidates (`app/modules/candidates.py`)
**What:** Intersects shot-cut timestamps with VAD silence windows. A candidate is kept only if:
1. The cut falls inside a VAD silence gap
2. There is ≥ `clearance_s` (0.1 s) of silence on both sides
3. The total gap width is ≥ `min_pause_s` (0.2 s)
4. The cut is not inside an ASR transcript chunk
5. The cut is outside the first/last `edge_exclusion_s` (60 s) of the video

Each candidate records `pause_before_s`, `pause_after_s`, the surrounding scene IDs, and whether it falls at a scene boundary.

**Why:** This deterministic filter removes the vast majority of shot cuts and ensures AI is only invoked for genuinely viable break points. The stricter the filter, the cheaper the downstream AI stages.

### M8 — Decide (`app/modules/decide.py`)
**What:** For each candidate, sends a short context window to TypeSafe Jev (or Gemini as fallback) asking four Noul/Score questions in a single batched call:
- `break_quality` — naturalness label (jarring → ideal)
- `emotional_peak` — probability the moment is emotionally intense
- `ends_scene` — probability the cut is at a scene end
- `sensitive_context` — probabilities of sensitive content around the cut

**Why:** The M5 scene model gives scene-level signals; M8 gives cut-level signals at higher precision. Using TypeSafe Jev (structured output, deterministic routing) over raw Gemini dramatically reduces hallucination on these probability estimates.

**Tools used:** TypeSafe Jev (`jev-latest`, structured probability outputs), Gemini 3.8 Flash fallback.

### M9 — Pacing (`app/modules/pacing.py`)
**What:** Pure Python — no AI. Takes all candidates with their M8 scores and applies:

1. **Hard gates** (reject the candidate outright):
   - Sensitive tag in prev or next scene with `p > sensitive_block_p (0.35)`
   - Emotional peak at the cut with `p > emotional_peak_reject_p (0.7)`

2. **Weighted score** (0–1 float, higher = better):
   | Feature | Weight |
   |---|---|
   | Pause length (capped at 3 s) | 0.25 |
   | Is a scene boundary | 0.25 |
   | Ends-scene probability | 0.20 |
   | Break quality (AI label) | 0.20 |
   | Low narrative tension | 0.10 |

3. **Greedy selection** (sorted by score descending):
   - Skip if within `min_gap_s` (30 s) of an already-selected break
   - Skip if adding this break would exceed `max_breaks_per_hour` (20) in any 60-min window
   - Skip if ad load would exceed `max_ad_load_pct` (25%)

**Why:** Pacing rules are deterministic and cheap to recompute. They enforce the business constraints (ad load, viewer fatigue) without AI cost. Separating scoring (M8) from selection (M9) means tuning the pacing budget requires zero API calls.

### M10 — Brands (`app/modules/brands.py`)
**What:** Matches selected break points to the brand catalogue through four sequential layers:

1. **Tag filter (hard block):** If a brand's `negative_contexts` matches any sensitive tag fired by M5/M8 around the cut (using a synonym table), the brand is blocked for this break.

2. **Keyword filter (hard block):** Regex-matches `negative_contexts` against `dominant_activity` and `setting` text from the surrounding scenes.

3. **Semantic filter (AI, Noul):** Asks the decider whether the scene description involves any of the brand's negative contexts. Brands with `p > 0.25` are blocked.

4. **AI ranking (AI, Choice):** Asks the decider to rank surviving brands by fit with the prev-scene activity and setting. Returns a probability distribution.

5. **Diversity allocation (greedy):** Instead of picking one winner per break, allocates one break per brand at its best available slot, respecting `min_gap_s`. Brands sorted by peak confidence get first pick.

6. **Reason (Gemini text):** Generates a one-sentence English explanation for each placement.

Each break's `decisions.brand_match` records the full trace: considered brands, blocked brands with reasons, semantic probabilities, ranking scores, and the assigned brand.

**Why:** The hard filters (layers 1–2) catch obvious safety violations cheaply. The semantic filter (layer 3) catches subtle cases (e.g., a cooking show scene with food spoilage for a food brand). Diversity allocation ensures every selected brand gets screen time rather than one brand dominating.

**Tools used:** TypeSafe Jev / Gemini 3.8 Flash for semantic filter and ranking.

### M11 — Manifest (`app/modules/manifest.py`)
**What:** Serialises the placement list into:
- `vmap.xml` — VMAP 1.0.1 with an inline VAST 4.2 `<AdParameters>` block per break, referencing the creative file
- `debug.json` — full pipeline trace (all candidates, decisions, scores, reasons, brand timelines)
- Slate fallback video for breaks with no creative asset

**Why:** VMAP/VAST is the industry-standard ad manifest format for video players. Debug JSON lets the UI display the full per-brand confidence timeline and per-break reasoning without needing a separate API.

---

## Brand catalogue format

Brands are loaded from `assets/brands.json` — a JSON array:

```json
{
  "brand_id": "brand_a",
  "display_name": "Brand A",
  "category": "food/spices/cooking",
  "target_contexts": ["cooking", "kitchen", "family meal", "recipe"],
  "negative_contexts": ["funeral", "hospital", "violence", "grief"],
  "creatives": [
    { "id": "a_15s_bn", "duration_sec": 15, "language": "bn", "url": "ads/brand_a/a_15s_bn.mp4" }
  ]
}
```

Adding a new brand requires only a new JSON entry — zero code changes. Brands can also be added at runtime via `POST /api/brands`.

---

## UI — BreakSense Light

A 3-column browser app served at `http://localhost:7860`:

- **Left column:** episode picker with thumbnails, brand selection checkboxes, "+ Add brand" form
- **Middle column:** video player that jumps to each break point on click, pipeline stats
- **Right column:** per-brand confidence timelines (colour-coded pips at each evaluated candidate), break cards with scenario label and confidence bar, tech details

Each break card shows the best-fit scenario label (Best fit ≥ 85%, Good fit ≥ 70%, Possible fit ≥ 55%, Weak fit otherwise) and a normalised confidence bar. The timeline pips distinguish: winner (red), high confidence (green ≥ 60%), medium (amber ≥ 35%), low (grey < 35%), and brand-safety blocked (grey tick).

---

## Configuration (`config.yaml`)

| Section | Key parameters |
|---|---|
| `models` | Gemini model IDs for scene analysis, text reasoning, ASR fallback |
| `flags` | `use_sarvam`, `use_jev` — toggle premium APIs |
| `vad` | `threshold`, `min_silence_duration_ms` (100), `min_speech_duration_ms` |
| `candidates` | `min_pause_s` (0.2), `clearance_s` (0.1), `edge_exclusion_s` (60) |
| `gates` | `sensitive_block_p` (0.35), `emotional_peak_reject_p` (0.7) |
| `scoring_weights` | per-feature weights summing to 1.0 |
| `pacing` | `max_breaks_per_hour` (20), `min_gap_s` (30), `max_ad_load_pct` (25) |
| `concurrency` | `gemini_max_parallel` (2 for free tier), `jev_max_parallel` (8) |

All thresholds are tunable without code changes. The cache system automatically re-runs only the stages downstream of changed sections.

---

## Tech stack

| Layer | Technology |
|---|---|
| Backend | Python 3.13, FastAPI, Uvicorn |
| Video processing | FFmpeg, PySceneDetect |
| Speech detection | Silero VAD 6.2 (local, CPU) |
| Transcription | Sarvam AI Saaras v3 (Bengali ASR) |
| Scene understanding | Google Gemini 3.8 Flash (agentic video) |
| Break scoring & brand matching | TypeSafe Jev (structured AI decisions) |
| Ad manifest | VMAP 1.0.1 + VAST 4.2 |
| UI | Vanilla JS, CSS Grid |
| Caching | JSON files on disk, SHA-256 + config-hash keys |

---

## Quick start

```bash
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt
cp .env.example .env          # add GEMINI_API_KEY (and optionally TYPESAFE_API_KEY, SARVAM_API_KEY)
python -m app.cli health       # verify ffmpeg + API keys
uvicorn app.api:app --port 7860
# open http://localhost:7860 — pick an episode, select brands, click Run
```

Full setup, Docker instructions, and troubleshooting: **[SETUP.md](SETUP.md)**

CLI usage:
```bash
python -m app.cli run --video assets/<episode>.mp4
# outputs: runs/<sha>/vmap.xml  runs/<sha>/debug.json
```

## API endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/runs` | Start a run (multipart `video` or `sample_id`) → `{run_id}` |
| `GET` | `/api/runs/{id}/events` | SSE stream of pipeline progress |
| `GET` | `/api/runs/{id}` | Full result JSON |
| `GET` | `/api/runs/{id}/vmap.xml` | VMAP manifest |
| `GET` | `/api/runs/{id}/debug.json` | Full pipeline trace |
| `POST` | `/api/runs/{id}/rematch` | Re-run pacing → brands → manifest from cache (seconds) |
| `GET\|POST` | `/api/brands` | List or add brands to the catalogue |
| `POST` | `/api/brands/upload` | Upload a brand creative file |
| `GET` | `/api/samples` | List available sample episodes |

## Tests

```bash
ruff check . && pytest -m "not live"    # offline: mocked AI responses
pytest -m live -s                      # hits real APIs
```
