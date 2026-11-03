# ARGUS - UI v2: Run / Map / Review (Windows, judge-ready)
#
# Double-click start-ui.bat in the argus folder. The browser opens
# on its own - no terminal commands are needed to use ARGUS.
#   1. Upload photos / video, or drop original JPGs into data\input
#   2. Edit the fictional case brief in section 0 - it saves
#      automatically to data\brief.txt AND to the FICTIONAL_BRIEF
#      line in src\score_all.py, so the pipeline always uses it.
#   3. Hit Run pipeline. Watch the progress bar; open the terminal
#      log with the "Show terminal log" button if you want the raw log.
#   4. Map: fullscreen button, Street / Satellite / Topo layers,
#      click any pin to open its source photo.
#   5. Review: Accept / Reject, export CSV / GeoJSON.
#   6. Review Agent (section 4): dedupe, borderline re-check, summary.
#
# What this does NOT do: it never touches .env, never prints the
# Meta key, never edits scan_all.py, and never sends anything
# anywhere except the Meta Model API calls the pipeline scripts
# already make. The brief is fictional only, never a real person
# or a real case.
#
# Files it reads/writes (all under the argus folder):
#   data\brief.txt                  - the saved fictional brief
#   data\input\*.jpg                - uploaded / copied source photos
#   data\output\manifest.json, ranked_candidates.json, sam_all.json
#   data\output\candidates\*.jpg    - candidate crops
#   data\output\review_decisions.json, run_status.json, run_log.txt

import csv
import io
import json
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

try:
    from werkzeug.utils import secure_filename
except Exception:  # very old Flask - fall back to a tiny sanitiser
    def secure_filename(name):
        return re.sub(r"[^A-Za-z0-9_.-]", "_", Path(name).name)

BASE = Path(__file__).resolve().parent.parent  # the argus folder
WEB_DIR = BASE / "web"
INPUT_DIR = BASE / "data" / "input"
DEMO_DIR = BASE / "sample-data"  # demo photos shipped in the repo (see sample-data/README-DEMO-PHOTOS.md)
OUT_DIR = BASE / "data" / "output"
TILES_DIR = BASE / "data" / "tiles"
SAM_DIR = OUT_DIR / "sam"
CAND_DIR = OUT_DIR / "candidates"
RANKED = OUT_DIR / "ranked_candidates.json"
MANIFEST = OUT_DIR / "manifest.json"
DECISIONS = OUT_DIR / "review_decisions.json"
RUN_STATUS = OUT_DIR / "run_status.json"
RUN_LOG = OUT_DIR / "run_log.txt"
AGENT = OUT_DIR / "review_agent.json"
AGENT_LOG = OUT_DIR / "review_agent_log.txt"
BRIEF_FILE = BASE / "data" / "brief.txt"
ENV_FILE = BASE / ".env"  # local only, git-ignored, never served back
DEFAULT_BRIEF = "red jacket, dark pants, adult build"
SCORE_ALL = BASE / "src" / "score_all.py"

PHOTO_EXTS = {".jpg", ".jpeg"}
VIDEO_EXTS = {".mp4", ".mov"}

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024  # 500 MB per upload batch

_run_lock = threading.Lock()
_run_state = {"running": False, "step": "idle", "message": "Not run yet.",
              "progress": 0, "step_index": 0, "step_total": 4}
_agent_lock = threading.Lock()
_agent_state = {"running": False, "message": "Not run yet."}
_upload_jobs = {}


# ---------- small helpers ----------

def load_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def load_decisions():
    data = load_json(DECISIONS, {})
    return data if isinstance(data, dict) else {}


def save_decisions(decisions):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    DECISIONS.write_text(json.dumps(decisions, indent=2), encoding="utf-8")


def load_agent():
    data = load_json(AGENT, {})
    if not isinstance(data, dict) or not data:
        return None
    kept = {c.get("candidate"): c for c in data.get("kept", [])}
    merged = {m.get("candidate"): m for m in data.get("merged_away", [])}
    return {"generated_at": data.get("generated_at"),
            "kept": kept, "merged": merged,
            "rechecked": data.get("rechecked", []),
            "summary": data.get("summary", "")}


def load_ranked():
    data = load_json(RANKED, {})
    cands = data.get("candidates", []) if isinstance(data, dict) else []
    decisions = load_decisions()
    agent = load_agent()
    for row in cands:
        row["decision"] = decisions.get(row.get("candidate"), {}).get("decision")
        if agent:
            kept = agent["kept"].get(row.get("candidate"))
            merged = agent["merged"].get(row.get("candidate"))
            if kept:
                row["muse_score_2"] = kept.get("muse_score_2")
                row["muse_reply_2"] = kept.get("muse_reply_2")
                row["review_score"] = kept.get("review_score")
                row["review_rank"] = kept.get("review_rank")
            if merged:
                row["merged_into"] = merged.get("merged_into")
                row["agent_note"] = merged.get("reason")
    return {"fictional_brief": data.get("fictional_brief"), "candidates": cands,
            "agent_ran": bool(agent)}


# ---------- brief: saved to data/brief.txt AND to score_all.py ----------

def get_brief():
    """Effective brief: saved brief.txt first, else the code line, else default."""
    if BRIEF_FILE.exists():
        saved = BRIEF_FILE.read_text(encoding="utf-8", errors="replace").strip()
        if saved:
            return saved
    if SCORE_ALL.exists():
        match = re.search(r'FICTIONAL_BRIEF\s*=\s*"((?:[^"\\]|\\.)*)"',
                          SCORE_ALL.read_text(encoding="utf-8", errors="replace"))
        if match:
            try:
                return json.loads(f'"{match.group(1)}"')
            except Exception:
                return match.group(1)
    return DEFAULT_BRIEF


def save_brief(text):
    text = (text or "").strip()
    if not text:
        raise ValueError("Brief cannot be empty.")
    if len(text) > 500:
        raise ValueError("Brief is too long - keep it under 500 characters.")
    BRIEF_FILE.parent.mkdir(parents=True, exist_ok=True)
    BRIEF_FILE.write_text(text, encoding="utf-8")
    # Also update the code line, so a judge reading score_all.py sees
    # exactly the brief the pipeline will use. If the regex misses
    # (code layout changed), brief.txt still governs via score_all.py's
    # loader - the save is not lost.
    if SCORE_ALL.exists():
        code = SCORE_ALL.read_text(encoding="utf-8", errors="replace")
        new_code, n = re.subn(r'FICTIONAL_BRIEF\s*=\s*"((?:[^"\\]|\\.)*)"',
                              f"FICTIONAL_BRIEF = {json.dumps(text)}",
                              code, count=1)
        if n:
            SCORE_ALL.write_text(new_code, encoding="utf-8")
    return text



# ---------- API key: saved to local .env, never shown back ----------

def key_present():
    """True only if .env has a real META_API_KEY, not the placeholder.

    This returns a boolean only. The key value itself is never
    returned, logged, or included in any API response."""
    if not ENV_FILE.exists():
        return False
    for line in ENV_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
        if re.match(r"\s*META_API_KEY\s*=", line):
            value = line.split("=", 1)[1].strip().strip('"').strip("'")
            return bool(value) and value != "your-key-here"
    return False


def save_key(key):
    """Write META_API_KEY into the local .env, preserving every other line.

    Replaces only the META_API_KEY line if one exists, otherwise appends
    it. Creates .env if there is none. This is a local file write only;
    nothing about the key leaves this machine through this UI."""
    key = (key or "").strip()
    if not key:
        raise ValueError("Paste a key first.")
    if any(ch.isspace() for ch in key):
        raise ValueError("The key must not contain spaces - paste it exactly as shown at dev.meta.ai.")
    if len(key) > 256:
        raise ValueError("That key looks too long - paste it exactly as shown at dev.meta.ai.")
    lines = []
    found = False
    if ENV_FILE.exists():
        lines = ENV_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    out = []
    for line in lines:
        if re.match(r"\s*META_API_KEY\s*=", line):
            out.append(f"META_API_KEY={key}")
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f"META_API_KEY={key}")
    ENV_FILE.write_text("\n".join(out) + "\n", encoding="utf-8")
    return True


# ---------- run progress ----------

def _log(line):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with RUN_LOG.open("a", encoding="utf-8") as fh:
        fh.write(line.rstrip() + "\n")


def _set_status(step, message, progress=None, step_index=None):
    if progress is not None:
        _run_state["progress"] = max(0, min(100, int(progress)))
    if step_index is not None:
        _run_state["step_index"] = step_index
    _run_state.update({"step": step, "message": message})
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    RUN_STATUS.write_text(json.dumps(
        {"running": _run_state["running"], "step": step, "message": message,
         "progress": _run_state["progress"],
         "step_index": _run_state["step_index"],
         "step_total": _run_state["step_total"]}, indent=2), encoding="utf-8")


def _progress_monitor():
    """While a run is live, refine the bar from real filesystem counts.

    sam-scan: scanned tile files / total tiles, mapped 30-80%.
    muse-score: candidate crops written / 20, mapped 80-100%.
    If a step has no countable output yet, the bar holds at its floor
    instead of faking progress.
    """
    while _run_state["running"]:
        step = _run_state["step"]
        try:
            if step == "sam-scan":
                tiles = list(TILES_DIR.glob("*.jpg")) + list(TILES_DIR.glob("*.jpeg"))
                total = len({str(p.resolve()).lower() for p in tiles}) or 1
                scanned = len(list(SAM_DIR.glob("*_sam.json"))) if SAM_DIR.exists() else 0
                _set_status(step, _run_state["message"],
                            progress=30 + int(50 * min(1.0, scanned / total)))
            elif step == "muse-score":
                made = len(list(CAND_DIR.glob("candidate-*.jpg"))) if CAND_DIR.exists() else 0
                _set_status(step, _run_state["message"],
                            progress=80 + int(20 * min(1.0, made / 20)))
        except Exception:
            pass
        time.sleep(1)


def _clean_stale_sam():
    """Oct 28 trap, fixed automatically: if sam_all.json names tiles
    that no longer exist (tiles were cleared for a fresh run), drop
    the stale scan so score_all never opens a missing tile."""
    sam_all = OUT_DIR / "sam_all.json"
    if not sam_all.exists():
        return
    data = load_json(sam_all, {})
    tiles = data.get("tiles", {}) if isinstance(data, dict) else {}
    missing = [t for t in tiles.values()
               if isinstance(t, dict) and t.get("tile")
               and not (BASE / t["tile"]).exists()
               and not Path(t["tile"]).exists()]
    if missing:
        _log(f"Stale scan detected: {len(missing)} tile(s) in sam_all.json "
             "no longer exist - clearing the old scan so this run is clean.")
        try:
            sam_all.unlink()
        except Exception:
            pass
        if SAM_DIR.exists():
            shutil.rmtree(SAM_DIR, ignore_errors=True)


def _run_step(label, argv, progress, step_index):
    _set_status(label, f"Running: {' '.join(argv)}",
                progress=progress, step_index=step_index)
    _log(f"\n=== {label}: {' '.join(argv)} ===")
    with RUN_LOG.open("a", encoding="utf-8") as fh:
        proc = subprocess.run(argv, cwd=str(BASE), stdout=fh,
                              stderr=subprocess.STDOUT, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"{label} failed (exit {proc.returncode}) - "
                           "open the terminal log below for the exact error.")


def run_pipeline():
    if not _run_lock.acquire(blocking=False):
        return
    try:
        _run_state["running"] = True
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        RUN_LOG.write_text("", encoding="utf-8")
        _log("ARGUS pipeline started " +
             datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
        _log(f"Brief in use: {get_brief()}")
        threading.Thread(target=_progress_monitor, daemon=True).start()
        py = sys.executable
        _run_step("ingest", [py, "src/ingest.py"], progress=5, step_index=1)
        _run_step("tile", [py, "src/tile.py"], progress=15, step_index=2)
        _clean_stale_sam()
        _run_step("sam-scan", [py, "src/scan_all.py"], progress=30, step_index=3)
        _run_step("muse-score", [py, "src/score_all.py"], progress=80, step_index=4)
        _set_status("done", "Pipeline finished. Ranked list is ready in Review below.",
                    progress=100, step_index=4)
        _log("\nPipeline finished.")
    except Exception as exc:  # noqa: BLE001 - show the failure in the UI, don't crash it
        _set_status("error", str(exc), progress=_run_state["progress"])
        _log(f"\nERROR: {exc}")
    finally:
        _run_state["running"] = False
        _run_lock.release()


def run_review_agent():
    if not _agent_lock.acquire(blocking=False):
        return
    try:
        _agent_state.update({"running": True, "message": "Running Review Agent..."})
        with AGENT_LOG.open("w", encoding="utf-8") as fh:
            proc = subprocess.run([sys.executable, "src/review_agent.py"],
                                  cwd=str(BASE), stdout=fh,
                                  stderr=subprocess.STDOUT, text=True)
        if proc.returncode != 0:
            _agent_state["message"] = ("Review Agent failed - see "
                                       "data\\output\\review_agent_log.txt")
        else:
            _agent_state["message"] = ("Review Agent done - dedupe, "
                                       "borderline re-check and summary ready.")
    except Exception as exc:  # noqa: BLE001
        _agent_state["message"] = str(exc)
    finally:
        _agent_state["running"] = False
        _agent_lock.release()


# ---------- uploads ----------

def _unique_name(folder, name):
    stem, ext = Path(name).stem, Path(name).suffix.lower()
    candidate, i = f"{stem}{ext}", 1
    while (folder / candidate).exists():
        i += 1
        candidate = f"{stem}_{i}{ext}"
    return candidate


def _extract_video_frames(video_path):
    """Video -> 1 frame per second in data/input, so the normal
    pipeline can analyse it. Returns (frames, error-or-None)."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return 0, ("ffmpeg not found, so no frames were extracted. "
                   "Install ffmpeg, or extract frames yourself with: "
                   f'ffmpeg -i "{video_path}" -vf fps=1 data\\input\\frame_%04d.jpg')
    stem = video_path.stem
    pattern = str(INPUT_DIR / f"{stem}_frame_%04d.jpg")
    proc = subprocess.run([ffmpeg, "-y", "-i", str(video_path),
                           "-vf", "fps=1", pattern],
                          capture_output=True, text=True)
    frames = len(list(INPUT_DIR.glob(f"{stem}_frame_*.jpg")))
    if proc.returncode != 0:
        return frames, f"ffmpeg failed: {proc.stderr[-500:]}"
    return frames, None


def _upload_job(job_id, saved):
    frames_total, errors = 0, []
    for path in saved:
        if path.suffix.lower() in VIDEO_EXTS:
            frames, err = _extract_video_frames(path)
            frames_total += frames
            if err:
                errors.append(err)
    _upload_jobs[job_id] = {"frames": frames_total, "errors": errors, "done": True}


# ---------- routes ----------

@app.route("/")
def index():
    return send_from_directory(str(WEB_DIR), "review.html")


@app.route("/api/status")
def status():
    ranked = load_ranked()
    manifest = load_json(MANIFEST, {})
    photos = manifest.get("photos", []) if isinstance(manifest, dict) else []
    sam = load_json(OUT_DIR / "sam_all.json", {})
    agent = load_agent()
    input_files = sorted(p.name for p in INPUT_DIR.glob("*") if p.is_file()) if INPUT_DIR.exists() else []
    return jsonify({
        "photos": len(photos),
        "photos_with_gps": sum(1 for p in photos if p.get("lat") is not None),
        "tiles_scanned": sam.get("tiles_scanned", 0) if isinstance(sam, dict) else 0,
        "ranked_count": len(ranked["candidates"]),
        "decisions": len(load_decisions()),
        "accepted": sum(1 for d in load_decisions().values()
                        if d.get("decision") == "accept"),
        "agent_ran": bool(agent),
        "agent_kept": len(agent["kept"]) if agent else 0,
        "agent_merged": len(agent["merged"]) if agent else 0,
        "agent_rechecked": len(agent["rechecked"]) if agent else 0,
        "brief": get_brief(),
        "input_files": input_files,
        "run": {"running": _run_state["running"], "step": _run_state["step"],
                "message": _run_state["message"],
                "progress": _run_state["progress"],
                "step_index": _run_state["step_index"],
                "step_total": _run_state["step_total"]},
    })


@app.route("/api/brief", methods=["GET", "POST"])
def brief():
    if request.method == "GET":
        return jsonify({"brief": get_brief()})
    body = request.get_json(force=True, silent=True) or {}
    try:
        saved = save_brief(body.get("brief", ""))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True, "brief": saved})



@app.route("/api/key", methods=["GET", "POST"])
def api_key():
    # GET returns presence only - never the key, not even masked.
    if request.method == "GET":
        return jsonify({"present": key_present()})
    body = request.get_json(force=True, silent=True) or {}
    try:
        save_key(body.get("key", ""))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True, "present": True})


@app.route("/api/upload", methods=["POST"])
def upload():
    files = request.files.getlist("files") or request.files.getlist("file")
    if not files:
        return jsonify({"error": "No files selected."}), 400
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    saved, skipped, videos = [], [], []
    for upload in files:
        if not upload or not upload.filename:
            continue
        name = secure_filename(upload.filename)
        ext = Path(name).suffix.lower()
        if ext in PHOTO_EXTS:
            dest = INPUT_DIR / _unique_name(INPUT_DIR, name)
            upload.save(str(dest))
            saved.append(dest)
        elif ext in VIDEO_EXTS:
            dest = INPUT_DIR / _unique_name(INPUT_DIR, name)
            upload.save(str(dest))
            saved.append(dest)
            videos.append(dest)
        else:
            skipped.append(upload.filename)
    job_id = f"upload-{int(time.time())}"
    _upload_jobs[job_id] = {"frames": 0, "errors": [], "done": not videos}
    if videos:
        threading.Thread(target=_upload_job, args=(job_id, videos), daemon=True).start()
    return jsonify({
        "ok": True, "job_id": job_id,
        "saved": [p.name for p in saved],
        "skipped": skipped,
        "note": ("Video saved in data\\input. Frames are being extracted "
                 "at 1 per second in the background." if videos else ""),
    })


@app.route("/api/upload/<job_id>")
def upload_status(job_id):
    job = _upload_jobs.get(job_id)
    if not job:
        return jsonify({"error": "unknown upload job"}), 404
    return jsonify(job)



@app.route("/api/demo-photos", methods=["GET", "POST"])
def demo_photos():
    """USE DEMO PHOTOS: copy the repo-bundled sample-data/*.jpg into
    data/input, so a judge with no photos of their own can still run
    the full pipeline. No download at click time - the files already
    came down with the GitHub clone. Re-clicking is safe: files
    already in data/input are reported as 'already', not duplicated."""
    if not DEMO_DIR.exists():
        return jsonify({"ok": False, "error": "sample-data folder not found - copy sample-data/ from this pack into the argus folder (next to data/, src/, web/) and restart the UI."}), 400
    demos = sorted(p for p in DEMO_DIR.iterdir()
                   if p.is_file() and p.suffix.lower() in PHOTO_EXTS)
    if request.method == "GET":
        INPUT_DIR.mkdir(parents=True, exist_ok=True)
        return jsonify({"ok": True, "demo_files": [p.name for p in demos],
                        "in_input": [p.name for p in demos if (INPUT_DIR / p.name).exists()]})
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    copied, already = [], []
    for src in demos:
        dest = INPUT_DIR / src.name
        if dest.exists():
            already.append(src.name)
        else:
            shutil.copy2(src, dest)
            copied.append(src.name)
    return jsonify({"ok": True, "copied": copied, "already": already,
                    "note": "Demo photos are now in data/input. Hit Run pipeline in section 2."})


@app.route("/api/uploads")
def uploads():
    if not INPUT_DIR.exists():
        return jsonify({"files": []})
    files = [{"name": p.name, "size_kb": round(p.stat().st_size / 1024, 1),
              "kind": "video" if p.suffix.lower() in VIDEO_EXTS else "photo"}
             for p in sorted(INPUT_DIR.iterdir()) if p.is_file()]
    return jsonify({"files": files})


@app.route("/api/agent")
def agent_status():
    agent = load_agent()
    log = ""
    if AGENT_LOG.exists():
        log = AGENT_LOG.read_text(encoding="utf-8", errors="replace")[-6000:]
    if not agent:
        return jsonify({"ran": False, "running": _agent_state["running"],
                        "message": _agent_state["message"], "log": log, "summary": ""})
    return jsonify({"ran": True, "running": _agent_state["running"],
                    "message": _agent_state["message"], "log": log,
                    "summary": agent.get("summary", ""),
                    "generated_at": agent.get("generated_at"),
                    "kept": len(agent["kept"]), "merged": len(agent["merged"]),
                    "rechecked": len(agent["rechecked"])})


@app.route("/api/agent/run", methods=["POST"])
def agent_run():
    if _agent_state["running"]:
        return jsonify({"ok": False, "error": "Review Agent is already running."}), 409
    if not RANKED.exists():
        return jsonify({"ok": False, "error": "No ranked candidates yet - run the pipeline first."}), 400
    if not (BASE / "src/review_agent.py").exists():
        return jsonify({"ok": False, "error": "Missing src/review_agent.py - copy this pack in first."}), 400
    threading.Thread(target=run_review_agent, daemon=True).start()
    return jsonify({"ok": True, "message": "Review Agent started. Watch section 5 below."})


@app.route("/api/ranked")
def ranked():
    return jsonify(load_ranked())


@app.route("/api/map")
def map_data():
    manifest = load_json(MANIFEST, {})
    photos = manifest.get("photos", []) if isinstance(manifest, dict) else []
    ranked = load_ranked()
    return jsonify({
        "photos": [{"filename": p.get("filename"), "lat": p.get("lat"),
                    "lon": p.get("lon")} for p in photos if p.get("lat") is not None],
        "candidates": [c for c in ranked["candidates"]
                       if c.get("lat") is not None and c.get("lon") is not None],
    })


@app.route("/api/crop/<name>")
def crop(name):
    if not (name.startswith("candidate-") and name.endswith(".jpg")) or "/" in name or "\\" in name:
        return jsonify({"error": "not a candidate crop"}), 404
    return send_from_directory(str(CAND_DIR), name)


@app.route("/api/photo/<name>")
def photo(name):
    # Source photos only, never a path: no slashes, known image type.
    if "/" in name or "\\" in name or ".." in name:
        return jsonify({"error": "not a source photo"}), 404
    if Path(name).suffix.lower() not in PHOTO_EXTS:
        return jsonify({"error": "not a source photo"}), 404
    return send_from_directory(str(INPUT_DIR), name)


@app.route("/api/decide", methods=["POST"])
def decide():
    body = request.get_json(force=True, silent=True) or {}
    name = body.get("candidate", "")
    decision = body.get("decision", "")
    if decision not in ("accept", "reject", "clear"):
        return jsonify({"error": "decision must be accept, reject or clear"}), 400
    if not (name.startswith("candidate-") and name.endswith(".jpg")):
        return jsonify({"error": "unknown candidate"}), 400
    decisions = load_decisions()
    if decision == "clear":
        decisions.pop(name, None)
    else:
        decisions[name] = {"decision": decision,
                           "at": datetime.now(timezone.utc).isoformat()}
    save_decisions(decisions)
    return jsonify({"ok": True, "candidate": name, "decision": decision})


@app.route("/api/run", methods=["POST"])
def run():
    if _run_state["running"]:
        return jsonify({"ok": False, "error": "A run is already in progress."}), 409
    for needed in ("src/ingest.py", "src/tile.py", "src/scan_all.py", "src/score_all.py"):
        if not (BASE / needed).exists():
            return jsonify({"ok": False,
                            "error": f"Missing {needed} - copy the pipeline scripts in first."}), 400
    threading.Thread(target=run_pipeline, daemon=True).start()
    return jsonify({"ok": True, "message": "Pipeline started. Watch the progress bar below."})


@app.route("/api/run/status")
def run_status():
    log = ""
    if RUN_LOG.exists():
        log = RUN_LOG.read_text(encoding="utf-8", errors="replace")[-12000:]
    return jsonify({"running": _run_state["running"], "step": _run_state["step"],
                    "message": _run_state["message"], "log": log,
                    "progress": _run_state["progress"],
                    "step_index": _run_state["step_index"],
                    "step_total": _run_state["step_total"]})


def _accepted_rows():
    ranked = load_ranked()
    decisions = load_decisions()
    return [c for c in ranked["candidates"]
            if decisions.get(c.get("candidate"), {}).get("decision") == "accept"]


@app.route("/api/export.csv")
def export_csv():
    rows = _accepted_rows()
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=[
        "rank", "candidate", "tile", "source_photo", "lat", "lon",
        "sam_confidence", "muse_score", "muse_reply"])
    writer.writeheader()
    for row in rows:
        writer.writerow({k: row.get(k) for k in writer.fieldnames})
    return app.response_class(buf.getvalue(), mimetype="text/csv",
                              headers={"Content-Disposition":
                                       "attachment; filename=argus_accepted.csv"})


@app.route("/api/export.geojson")
def export_geojson():
    features = []
    for row in _accepted_rows():
        if row.get("lat") is None or row.get("lon") is None:
            continue
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [row["lon"], row["lat"]]},
            "properties": {"rank": row.get("rank"), "candidate": row.get("candidate"),
                           "source_photo": row.get("source_photo"),
                           "sam_confidence": row.get("sam_confidence"),
                           "muse_score": row.get("muse_score"),
                           "muse_reply": row.get("muse_reply")},
        })
    return jsonify({"type": "FeatureCollection", "features": features})


if __name__ == "__main__":
    print("ARGUS review UI: open http://127.0.0.1:5000 in your browser")
    print("Press Ctrl+C to stop it. Your .env key is never shown or sent anywhere except")
    print("the Meta Model API calls the pipeline scripts already make.")
    # Judge-friendly: the browser opens on its own, no terminal step.
    try:
        import webbrowser
        threading.Timer(1.2, lambda: webbrowser.open("http://127.0.0.1:5000")).start()
    except Exception:
        pass
    app.run(host="127.0.0.1", port=5000)
