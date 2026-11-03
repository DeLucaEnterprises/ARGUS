# ARGUS — Aerial Review & Ground-imagery Understanding for Search

ARGUS reviews a search team's own drone and grid photos so a human does not have to
look at thousands of raw images one by one. It finds candidate objects with SAM 3.1,
checks each candidate against a written case brief with Muse Spark, and ranks them on
a map and in a review list. A human verifies every candidate. AI never calls a find.

**Guardrails, in every use:** detection, never identification. No facial recognition.
The brief is fictional test data only in this repo — never a real person or a real case.
The Review Agent proposes; a human accepts or rejects each candidate.

Built during the Meta Global AI Developer Hackathon, October 26 – November 4, 2026,
with SAM 3.1 and Muse Spark via the Meta Model API.

## Try it in 5 minutes (no photos needed)

You need: Windows, Python 3.11 or newer, Git, and a Meta Model API key
(free at https://dev.meta.ai/api-keys). Video support also needs ffmpeg, but photos
work without it.

1. Clone the repo, then double-click `setup.bat`.
   It creates the virtual environment, installs the requirements, creates the
   `data\` folders, and — only if you do not already have one — creates `.env`
   from `.env.example`. It never overwrites an existing `.env`.
2. Double-click `start-ui.bat`. Leave that window open. The browser opens on its own
   at http://127.0.0.1:5000. If it does not open, go to that address yourself.
3. At the top of the page, in **API key**, paste your Meta key and click Save key.
   It is written to the local `.env` file only — it is never displayed back, never
   logged, and never committed (`.env` is git-ignored). If you prefer Notepad, you
   can instead edit `.env` by hand; the UI field and the file are the same key.
4. Section 1, click **USE DEMO PHOTOS**. That copies the 3 demo drone photos in
   `sample-data\` into `data\input\` — the folder ARGUS analyses.
5. Section 0, type the brief. A good first test for the demo set is
   `adult person, dark shirt`. It saves automatically.
6. Section 2, click **Run pipeline**. Watch the percentage bar: ingest, tiling,
   SAM scan, Muse score.
7. Section 3, Map: switch Street / Satellite / Topo, click pins to open source photos.
   Section 4, Review: Accept or Reject each ranked candidate, export CSV / GeoJSON.
   Section 5, Run Review Agent: it dedupes overlapping-tile duplicates, re-checks
   borderline Muse scores (0.4–0.6) with one second pass, and writes a plain-text
   summary to `data\output\review_summary.txt`. You still decide each candidate.

That is the whole first run. No terminal commands after `setup.bat`.

## Use your own photos or video

Section 1, upload JPG photos or MP4 / MOV video in the page, or drop original JPGs
into `data\input\` yourself — both land in the same folder. Video is turned into
1 frame per second in the background. If ffmpeg is missing, the video is still saved
and the page tells you plainly, with the manual ffmpeg command.

Then brief, Run, Map, Review, Review Agent, exactly as above.

## What is in this repo

| Path | What it is |
|---|---|
| `src\ingest.py` | Reads `data\input\`, records each photo and its GPS (when the photo has it) |
| `src\tile.py` | Cuts photos into 1024px tiles, high-quality JPEG, no resizing |
| `src\scan_all.py` | SAM 3.1 scan over every tile, prompt "person" |
| `src\score_all.py` | Crops the top SAM boxes from the original photo, Muse-scores them against the brief |
| `src\app.py` | The Flask UI backend (brief, upload, demo photos, run, map, review, agent, export) |
| `src\review_agent.py` | Review Agent: dedupe, borderline re-check, plain-text summary |
| `src\benchmark.py` | Offline benchmark: photo-level recall at Muse thresholds, plus false alarms |
| `web\review.html` | The UI page |
| `sample-data\` | Demo drone photos, committed on purpose so a fresh clone can run |
| `benchmark_truth_template.csv` | Template for the benchmark truth file — only photos with known ground truth go in it |
| `setup.bat`, `start-ui.bat` | One-time setup, and the double-click UI launcher |
| `requirements.txt` | Pillow, Flask, openai, meta-sam-parser |
| `.env.example` | Copy to `.env`, add your key — never commit `.env` |

## Troubleshooting

- **"You must be in the argus folder"** — you double-clicked a `.bat` from the wrong
  folder. Run it from the folder that contains `src\`, `web\`, `sample-data\` and
  `start-ui.bat`, not from its parent.
- **Port 5000 already in use, or the page loads but a button returns a web page
  instead of data** — an old UI server is still running. Close *every* ARGUS command
  window, not just the browser tab, then run `start-ui.bat` again.
- **Run stops naming a missing file** — the pipeline scripts must all be present
  (`src\ingest.py`, `src\tile.py`, `src\scan_all.py`, `src\score_all.py`).
  Re-copy them from this repo and run again.
- **No map pins for a photo** — map pins only appear for photos that carry GPS.
  Photos without GPS still tile, scan and score; they just do not pin.
- **Video produces no frames** — install ffmpeg, or use the manual command the
  page shows. Photos do not need ffmpeg.

## Benchmark (optional)

Copy `benchmark_truth_template.csv` to `data\benchmark_truth.csv`, fill one row per
photo you actually know the truth for, then run:

```
.venv\Scripts\python src\benchmark.py
```

Quote any result as a fraction, labelled test-subjects-only. Never drop a missed
photo to improve the score.

## Licence

MIT — see `LICENSE`. If you use this code, keep the guardrails above: detection,
never identification, no facial recognition, human verifies every candidate.
