# ARGUS - Day 5: Review Agent (Windows)
#
# The small, verifiable agent layer Mark approved Oct 28, 2026.
# It sits ON TOP of the Days 3-4 ranked list. It does NOT rebuild
# the pipeline, does NOT pick the brief, and does NOT run a search
# on its own. It proposes; a human approves or rejects each one.
#
# Three jobs only, in order:
#   1. Dedupe - tiles overlap by 128px, so the same real object can
#      appear as a candidate in two neighbouring tiles. Candidates
#      from the same source photo whose boxes overlap heavily in
#      global photo coordinates (IoU >= 0.50) are grouped; the best
#      one is kept, the rest are marked merged-away, with the IoU
#      and the tile names recorded so a human can check the call.
#   2. Re-check the borderline ones - surviving candidates with a
#      Muse score in 0.40-0.60 get ONE second Muse pass with a
#      tighter prompt. The original score is kept; the second score
#      is stored separately as muse_score_2, and the review score
#      is the second score when it exists, the first score otherwise.
#   3. Draft the review summary - one line per surviving candidate,
#      each with a citation to the tile, crop, SAM confidence and
#      Muse score(s), so a human can verify every claim in one click.
#      Merged-away duplicates get their own line, citing why.
#
# What it writes (it never overwrites ranked_candidates.json):
#   data/output/review_agent.json  - the full agent proposal
#   data/output/review_summary.md  - the one-line-per-candidate summary
# The Day 5 UI reads review_agent.json and shows the agent notes on
# each card. The original ranked list stays the audit trail.
#
# Run from the argus folder, with the venv on, AFTER Days 3-4:
#   .venv\Scripts\python src\review_agent.py
# Or from the Day 5 UI: section 4, "Run Review Agent".
# No key is needed for dedupe + summary; the second Muse pass is
# skipped (with a warning) if .env has no Meta key yet.
#
# Fictional brief only - never a real person or a real case.

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

RANKED = Path("data/output/ranked_candidates.json")
TILES = Path("data/output/tiles.json")
OUT_JSON = Path("data/output/review_agent.json")
OUT_MD = Path("data/output/review_summary.md")

TILE = 1024
OVERLAP = 128
STEP = TILE - OVERLAP
IOU_THRESHOLD = 0.50
BORDERLINE_LOW = 0.40
BORDERLINE_HIGH = 0.60

SCORE_RE = re.compile(r"0(?:\.\d+)?|1(?:\.0+)?")
TILE_RC_RE = re.compile(r"_t(\d+)_(\d+)$")


def load_env(path=".env"):
    env = {}
    p = Path(path)
    if not p.exists():
        return env
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def get_key():
    import os

    key = load_env().get("META_API_KEY") or os.environ.get("META_API_KEY", "")
    if not key or key == "your-key-here":
        return None
    return key


def tile_origins():
    """tile name -> (x, y) origin in the source photo, from tiles.json."""
    origins = {}
    if TILES.exists():
        try:
            data = json.loads(TILES.read_text(encoding="utf-8"))
            for t in data.get("tiles", []):
                origins[t.get("tile")] = (t.get("x", 0), t.get("y", 0))
        except Exception:
            pass
    return origins


def global_box(cand, origins):
    """Tile-local box -> global photo coordinates, using the tile origin."""
    box = cand.get("box") or [0, 0, 0, 0]
    tile = cand.get("tile", "")
    if tile in origins:
        ox, oy = origins[tile]
    else:
        # Fallback: parse _t<row>_<col> from the tile name; step is 896px.
        m = TILE_RC_RE.search(Path(tile).stem)
        if m:
            row, col = int(m.group(1)), int(m.group(2))
            ox, oy = col * STEP, row * STEP
        else:
            ox, oy = 0, 0
    return (box[0] + ox, box[1] + oy, box[2] + ox, box[3] + oy)


def iou(a, b):
    x1 = max(a[0], b[0])
    y1 = max(a[1], b[1])
    x2 = min(a[2], b[2])
    y2 = min(a[3], b[3])
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    if inter == 0:
        return 0.0
    area_a = max(0, a[2] - a[0]) * max(0, a[3] - a[1])
    area_b = max(0, b[2] - b[0]) * max(0, b[3] - b[1])
    union = area_a + area_b - inter
    return inter / union if union else 0.0


def dedupe(candidates, origins):
    """Group same-photo candidates with IoU >= threshold. Returns
    (kept, merged_away) where merged_away entries record why."""
    kept = []
    merged = []
    for cand in candidates:
        cand = dict(cand)
        cand["_gbox"] = global_box(cand, origins)
        match = None
        match_iou = 0.0
        for other in kept:
            if other.get("source_photo") != cand.get("source_photo"):
                continue
            v = iou(cand["_gbox"], other["_gbox"])
            if v >= IOU_THRESHOLD and v > match_iou:
                match, match_iou = other, v
        if match is None:
            kept.append(cand)
            continue
        # Keep the stronger one: higher Muse score first, SAM breaks ties.
        def strength(c):
            s = c.get("muse_score")
            return (s is not None, s if s is not None else -1,
                    c.get("sam_confidence") or 0)
        if strength(cand) > strength(match):
            kept.remove(match)
            merged.append({
                "candidate": match.get("candidate"),
                "merged_into": cand.get("candidate"),
                "reason": (f"same object as {cand.get('candidate')} "
                           f"(same photo {cand.get('source_photo')}, "
                           f"IoU {match_iou:.2f} >= {IOU_THRESHOLD}, "
                           f"tile {match.get('tile')} vs {cand.get('tile')})"),
                "iou": round(match_iou, 3),
            })
            kept.append(cand)
        else:
            merged.append({
                "candidate": cand.get("candidate"),
                "merged_into": match.get("candidate"),
                "reason": (f"same object as {match.get('candidate')} "
                           f"(same photo {cand.get('source_photo')}, "
                           f"IoU {match_iou:.2f} >= {IOU_THRESHOLD}, "
                           f"tile {cand.get('tile')} vs {match.get('tile')})"),
                "iou": round(match_iou, 3),
            })
    for cand in kept:
        cand.pop("_gbox", None)
    return kept, merged


def verify_second_pass(key, candidate_path, brief):
    """One tighter Muse pass on a borderline crop. Returns (score, reply)."""
    import base64

    from openai import OpenAI

    raw = Path(candidate_path).read_bytes()
    data_url = "data:image/jpeg;base64," + base64.b64encode(raw).decode("ascii")
    prompt = (
        "Fictional test case - second look. Look carefully at this cropped image "
        "and decide ONLY whether it shows a person matching: "
        f"{brief}. "
        "Check each part of the description separately (clothing colours, build) "
        "and do not guess from one feature alone. Reply with a score 0-1 "
        "and exactly one sentence why. This is a synthetic test image, "
        "not a real person."
    )
    client = OpenAI(base_url="https://api.meta.ai/v1", api_key=key)
    resp = client.chat.completions.create(
        model="muse-spark-1.3",
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        }],
    )
    reply = resp.choices[0].message.content or ""
    match = SCORE_RE.search(reply) if reply else None
    return (float(match.group(0)) if match else None), reply


def summary_line(row):
    gps = (f"GPS {row.get('lat')}, {row.get('lon')}"
           if row.get("lat") is not None else "no GPS")
    second = ""
    if row.get("muse_score_2") is not None:
        second = f" -> 2nd Muse {row['muse_score_2']}"
    return (f"- #{row.get('rank')} {row.get('candidate')} - "
            f"Muse {row.get('muse_score')}{second} - "
            f"SAM {row.get('sam_confidence')} - "
            f"tile {row.get('tile')} - from {row.get('source_photo')} - "
            f"{gps} - Muse: \"{row.get('muse_reply', '').strip()[:120]}\" "
            f"[human decision: ___]")


def main():
    if not RANKED.exists():
        print("ERROR: data\\output\\ranked_candidates.json not found.")
        print("Run Days 3-4 first: .venv\\Scripts\\python src\\score_all.py")
        sys.exit(2)
    ranked = json.loads(RANKED.read_text(encoding="utf-8"))
    candidates = ranked.get("candidates", [])
    brief = ranked.get("fictional_brief", "")
    print(f"Review Agent: {len(candidates)} ranked candidate(s), "
          f"brief: {brief!r}")

    origins = tile_origins()
    kept, merged = dedupe(candidates, origins)
    print(f"Dedupe: kept {len(kept)}, merged away {len(merged)} duplicate(s).")
    for m in merged:
        print(f"  merged {m['candidate']} into {m['merged_into']}: {m['reason']}")

    # Re-check borderline survivors, one second Muse pass each.
    key = get_key()
    rechecked = []
    skipped = []
    for cand in kept:
        score = cand.get("muse_score")
        if score is None or not (BORDERLINE_LOW <= score <= BORDERLINE_HIGH):
            continue
        crop = Path("data/output/candidates") / cand.get("candidate", "")
        if not crop.exists():
            skipped.append({"candidate": cand.get("candidate"),
                            "reason": "crop file not found"})
            continue
        if key is None:
            skipped.append({"candidate": cand.get("candidate"),
                            "reason": "no Meta key in .env - second pass skipped"})
            continue
        print(f"  2nd pass {cand.get('candidate')} (Muse {score}) ...", flush=True)
        try:
            score2, reply2 = verify_second_pass(key, crop, brief)
        except Exception as exc:  # noqa: BLE001 - one bad call must not kill the run
            print(f"    ERROR calling Muse: {exc} - keeping first score.")
            skipped.append({"candidate": cand.get("candidate"),
                            "reason": f"Muse call failed: {exc}"})
            continue
        cand["muse_score_2"] = score2
        cand["muse_reply_2"] = reply2
        rechecked.append({"candidate": cand.get("candidate"),
                          "muse_score": score, "muse_score_2": score2})
        print(f"    2nd Muse score: {score2}  {reply2.strip()[:120]}")
    if key is None:
        print("No Meta key in .env - borderline second passes skipped, "
              "dedupe + summary still done.")
    for s in skipped:
        print(f"  skipped 2nd pass {s['candidate']}: {s['reason']}")

    # Review score = second score when it exists, else the first score.
    for cand in kept:
        cand["review_score"] = (cand["muse_score_2"]
                                if cand.get("muse_score_2") is not None
                                else cand.get("muse_score"))
    kept.sort(key=lambda r: (r["review_score"] is not None,
                             r["review_score"] if r["review_score"] is not None else -1,
                             r.get("sam_confidence") or 0), reverse=True)
    for i, row in enumerate(kept, start=1):
        row["review_rank"] = i

    merged_by_name = {m["candidate"]: m for m in merged}
    lines = [
        "# ARGUS Review Agent - summary",
        "",
        f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} - "
        f"fictional brief: {brief!r}",
        f"Ranked: {len(candidates)} - kept: {len(kept)} - "
        f"merged away: {len(merged)} - borderline re-checked: {len(rechecked)}",
        "",
        "Agent proposes, human decides. Nothing here is a find - "
        "Accept / Reject each candidate in the Day 5 UI.",
        "",
        "## Surviving candidates (by review score)",
        "",
    ]
    for row in kept:
        # Show the review rank in the line, keep the original rank too.
        line_row = dict(row)
        line_row["rank"] = row.get("review_rank")
        lines.append(summary_line(line_row))
    lines += ["", "## Merged away (duplicates - check before trusting)", ""]
    if merged:
        for m in merged:
            lines.append(f"- {m['candidate']} - {m['reason']}")
    else:
        lines.append("- None. No two candidates overlapped enough to merge.")
    if skipped:
        lines += ["", "## Second pass skipped", ""]
        for s in skipped:
            lines.append(f"- {s['candidate']} - {s['reason']}")
    lines.append("")

    OUT_JSON.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "fictional_brief": brief,
        "kept": kept,
        "merged_away": merged,
        "rechecked": rechecked,
        "skipped_second_pass": skipped,
        "summary": "\n".join(lines),
    }, indent=2), encoding="utf-8")
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print()
    print("Review Agent done:")
    print(f"  kept {len(kept)}, merged {len(merged)}, "
          f"re-checked {len(rechecked)}, skipped {len(skipped)}")
    print(f"Saved: {OUT_JSON}")
    print(f"Saved: {OUT_MD}")
    print("Open the Day 5 UI and check the agent notes on each card - "
          "then Accept / Reject as usual.")


if __name__ == "__main__":
    main()
