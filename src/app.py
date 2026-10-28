# ARGUS - Day 5: Run / Map / Review UI (Windows)
#
# A tiny Flask backend for the review UI Mark approved Oct 27, 2026.
# The old map (web/index.html, python -m http.server) is read-only:
# it can show pins but cannot run anything. This app is the same
# scripts underneath, triggered from the browser instead of the
# command line.
#
# One page, three parts:
#   Run    - hit Run, it runs ingest -> tile -> SAM scan -> Muse score,
#            with a progress log (the exact same src/*.py files).
#   Map    - pins coloured by Muse score, so top candidates stand out.
#   Review - ranked cards, top score first. Click Accept or Reject
#            on each. Export the accepted ones as CSV / GeoJSON.
#
# What this does NOT do: it never edits src/scan_all.py or
# src/score_all.py, never touches .env, never prints the Meta key.
# The fictional brief is the one already at the top of
# src/score_all.py - edit it there before you hit Run if you want a
# different one. Fictional only, never a real person or a real case.
#
# Run from the argus folder, with the venv on:
#   .venv\Scripts\python -m pip install flask
#   .venv\Scripts\python src\app.py
# Or double-click start-ui.bat in the argus folder (does both).
# Then open http://127.0.0.1:5000 in your browser.
#
# Files it reads/writes (all under the argus folder):
#   reads  data/output/manifest.json, data/output/ranked_candidates.json
#   reads  data/output/candidates/candidate-*.jpg  (crop images)
#   writes data/output/review_decisions.json  (your Accept/Reject calls)
#   writes data/output/run_status.json + run_log.txt (Run progress)

import csv
import io
import json
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

BASE = Path(__file__).resolve().parent.parent  # the argus folder
WEB_DIR = BASE / "web"
OUT_DIR = BASE / "data" / "output"
CAND_DIR = OUT_DIR / "candidates"
RANKED = OUT_DIR / "ranked_candidates.json"
MANIFEST = OUT_DIR / "manifest.json"
DECISIONS = OUT_DIR / "review_decisions.json"
RUN_STATUS = OUT_DIR / "run_status.json"
RUN_LOG = OUT_DIR / "run_log.txt"
AGENT = OUT_DIR / "review_agent.json"
AGENT_LOG = OUT_DIR / "review_agent_log.txt"

app = Flask(__name__)

# --- Run state (one run at a time; a second click while running is ignored) ---
_run_lock = threading.Lock()
_run_state = {"running": False, "step": "idle", "message": "Not run yet."}
_agent_lock = threading.Lock()
_agent_state = {"running": False, "message": "Not run yet."}


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
    """The Review Agent proposal, if it has been run. Never overwrites
    the ranked list - it is an overlay the UI shows on each card."""
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


def _log(line):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with RUN_LOG.open("a", encoding="utf-8") as fh:
        fh.write(line.rstrip() + "\n")


def _set_status(step, message):
    _run_state.update({"step": step, "message": message})
    RUN_STATUS.write_text(json.dumps(
        {"running": _run_state["running"], "step": step, "message": message},
        indent=2), encoding="utf-8")


def _run_step(label, argv):
    """Run one pipeline script, streaming its output to the log file."""
    _set_status(label, f"Running: {' '.join(argv)}")
    _log(f"\n=== {label}: {' '.join(argv)} ===")
    with RUN_LOG.open("a", encoding="utf-8") as fh:
        proc = subprocess.run(argv, cwd=str(BASE), stdout=fh,
                              stderr=subprocess.STDOUT, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"{label} failed (exit {proc.returncode}) - "
                           "see the log below, and the README troubleshooting section.")


def run_pipeline():
    """ingest -> tile -> SAM scan -> Muse score, in a background thread."""
    if not _run_lock.acquire(blocking=False):
        return
    try:
        _run_state["running"] = True
        RUN_LOG.write_text("", encoding="utf-8")
        _log("ARGUS pipeline started " +
             datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
        py = sys.executable
        _run_step("ingest", [py, "src/ingest.py"])
        _run_step("tile", [py, "src/tile.py"])
        _run_step("sam-scan", [py, "src/scan_all.py"])
        _run_step("muse-score", [py, "src/score_all.py"])
        _set_status("done", "Pipeline finished. Ranked list is ready in Review below.")
        _log("\nPipeline finished.")
    except Exception as exc:  # noqa: BLE001 - show the failure in the UI, don't crash it
        _set_status("error", str(exc))
        _log(f"\nERROR: {exc}")
    finally:
        _run_state["running"] = False
        _run_lock.release()


def run_review_agent():
    """Run src/review_agent.py once, in a background thread."""
    if not _agent_lock.acquire(blocking=False):
        return
    try:
        _agent_state.update({"running": True,
                             "message": "Running Review Agent..."})
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
    except Exception as exc:  # noqa: BLE001 - show the failure, don't crash the UI
        _agent_state["message"] = str(exc)
    finally:
        _agent_state["running"] = False
        _agent_lock.release()


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
        "run": {"running": _run_state["running"], "step": _run_state["step"],
                "message": _run_state["message"]},
    })


@app.route("/api/agent")
def agent_status():
    agent = load_agent()
    log = ""
    if AGENT_LOG.exists():
        log = AGENT_LOG.read_text(encoding="utf-8", errors="replace")[-6000:]
    if not agent:
        return jsonify({"ran": False, "running": _agent_state["running"],
                        "message": _agent_state["message"], "log": log,
                        "summary": ""})
    return jsonify({"ran": True, "running": _agent_state["running"],
                    "message": _agent_state["message"], "log": log,
                    "summary": agent.get("summary", ""),
                    "generated_at": agent.get("generated_at"),
                    "kept": len(agent["kept"]), "merged": len(agent["merged"]),
                    "rechecked": len(agent["rechecked"])})


@app.route("/api/agent/run", methods=["POST"])
def agent_run():
    if _agent_state["running"]:
        return jsonify({"ok": False,
                        "error": "Review Agent is already running."}), 409
    if not RANKED.exists():
        return jsonify({"ok": False,
                        "error": "No ranked candidates yet - run Days 3-4 first."}), 400
    if not (BASE / "src/review_agent.py").exists():
        return jsonify({"ok": False,
                        "error": "Missing src/review_agent.py - copy this pack in first."}), 400
    threading.Thread(target=run_review_agent, daemon=True).start()
    return jsonify({"ok": True,
                    "message": "Review Agent started. Watch section 4 below."})


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
    # Only serve files that look like candidate-01.jpg, never a path.
    if not (name.startswith("candidate-") and name.endswith(".jpg")) or "/" in name or "\\" in name:
        return jsonify({"error": "not a candidate crop"}), 404
    return send_from_directory(str(CAND_DIR), name)


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
        decisions[name] = {
            "decision": decision,
            "at": datetime.now(timezone.utc).isoformat(),
        }
    save_decisions(decisions)
    return jsonify({"ok": True, "candidate": name, "decision": decision})


@app.route("/api/run", methods=["POST"])
def run():
    if _run_state["running"]:
        return jsonify({"ok": False, "error": "A run is already in progress."}), 409
    for needed in ("src/ingest.py", "src/tile.py", "src/scan_all.py", "src/score_all.py"):
        if not (BASE / needed).exists():
            return jsonify({"ok": False,
                            "error": f"Missing {needed} - copy the Days 2 and 3-4 packs in first."}), 400
    threading.Thread(target=run_pipeline, daemon=True).start()
    return jsonify({"ok": True, "message": "Pipeline started. Watch the log below."})


@app.route("/api/run/status")
def run_status():
    log = ""
    if RUN_LOG.exists():
        log = RUN_LOG.read_text(encoding="utf-8", errors="replace")[-12000:]
    return jsonify({"running": _run_state["running"], "step": _run_state["step"],
                    "message": _run_state["message"], "log": log})


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
            "geometry": {"type": "Point",
                         "coordinates": [row["lon"], row["lat"]]},
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
    app.run(host="127.0.0.1", port=5000)
