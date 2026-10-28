# ARGUS - Days 3-4, Part 2: Muse-score the top SAM candidates (Windows)
#
# Run AFTER Part 1 (scan_all.py) has scanned all tiles.
#
# What this does, in order:
#   1. Reads data\output\sam_all.json (every tile's SAM boxes).
#   2. Takes the top candidates by SAM confidence (default: top 20,
#      confidence >= 0.80), highest first.
#   3. Crops each candidate out of its tile and saves it as
#      data\output\candidates\candidate-01.jpg, candidate-02.jpg, ...
#   4. Sends each crop to Muse Spark (muse-spark-1.3, Standard) with
#      the fictional case brief below, and asks for a score 0-1 plus
#      one sentence why - the exact same call as Step 6 (verify_test.py).
#   5. Writes the ranked list, Muse score first, to:
#      data\output\ranked_candidates.json  (full detail, incl. Muse reply)
#      data\output\ranked_candidates.csv   (one row per candidate, with
#      source photo, GPS from manifest.json where the photo has GPS,
#      SAM confidence, Muse score, Muse reason)
#
# GPS note: video-frame tiles (frame_*.jpg) have no EXIF GPS, so their
# rows show lat/lon empty - same as Day 2, the map skips them. GPS
# photos show their lat/lon, so a ranked candidate can be pinned.
#
# Known limitation, by design: tiles overlap by 128px, so the same
# real object can appear as a candidate in two neighbouring tiles.
# This file does NOT dedupe across tiles - Day 5 review is where a
# human (or a dedupe pass) merges those. The ranked list is the input
# to that review, not the final answer.
#
# EDIT THE BRIEF FIRST: replace FICTIONAL_BRIEF with your fictional test
# description (clothing colours, build). Fictional only - never a real
# person or a real case. Default is the Day 1 brief from verify_test.py.
#
# Run from the argus folder, with the venv on:
#   .venv\Scripts\python src\score_all.py

import base64
import csv
import json
import re
import sys
from pathlib import Path

from PIL import Image

SAM_ALL = Path("data/output/sam_all.json")
MANIFEST = Path("data/output/manifest.json")
CAND_DIR = Path("data/output/candidates")
OUT_JSON = Path("data/output/ranked_candidates.json")
OUT_CSV = Path("data/output/ranked_candidates.csv")

# --- EDIT THIS: fictional test case only, never a real person/case ---
FICTIONAL_BRIEF = "black shirt, shorts, 10 year old build"

# How many candidates to score, and the SAM confidence floor.
TOP_N = 20
MIN_CONFIDENCE = 0.80

SCORE_RE = re.compile(r"0(?:\.\d+)?|1(?:\.0+)?")
TILE_RE = re.compile(r"^(.+)_t\d+_\d+$")


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
        print("ERROR: no Meta key in .env yet. Put it in .env first (see Step 5).")
        sys.exit(2)
    return key


def source_photo_for(tile_name):
    """test-01_t1_2.jpg -> test-01.jpg ; frame_0003_t0_1.jpg -> frame_0003.jpg"""
    stem = Path(tile_name).stem
    match = TILE_RE.match(stem)
    return (match.group(1) + ".jpg") if match else tile_name


def gps_for(manifest, photo_name):
    for photo in manifest.get("photos", []):
        if photo.get("filename") == photo_name:
            return photo.get("lat"), photo.get("lon")
    return None, None


def verify_with_muse(key, candidate_path):
    from openai import OpenAI

    raw = Path(candidate_path).read_bytes()
    data_url = "data:image/jpeg;base64," + base64.b64encode(raw).decode("ascii")
    prompt = (
        "Fictional test case. Does this cropped image show a person matching: "
        f"{FICTIONAL_BRIEF}? Reply with a score 0-1 and one sentence why. "
        "This is a synthetic test image, not a real person."
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
    return resp.choices[0].message.content or ""


def main():
    if not SAM_ALL.exists():
        print(f"ERROR: {SAM_ALL} not found. Run Part 1 first:")
        print("  .venv\\Scripts\\python src\\scan_all.py")
        sys.exit(2)
    sam_all = json.loads(SAM_ALL.read_text(encoding="utf-8"))
    manifest = {}
    if MANIFEST.exists():
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    # Collect every SAM box, highest confidence first.
    pool = []
    for tile_name, payload in sam_all.get("tiles", {}).items():
        tile_path = Path(payload.get("tile", f"data/tiles/{tile_name}.jpg"))
        for det in payload.get("detections", []):
            if not det.get("box"):
                continue
            conf = det.get("confidence")
            if conf is None or conf < MIN_CONFIDENCE:
                continue
            pool.append({"tile": tile_name, "tile_path": str(tile_path),
                         "object_id": det.get("object_id"), "box": det["box"],
                         "confidence": conf})
    pool.sort(key=lambda d: d["confidence"], reverse=True)
    chosen = pool[:TOP_N]

    print(f"SAM boxes at confidence >= {MIN_CONFIDENCE}: {len(pool)}")
    print(f"Scoring top {len(chosen)} with Muse Spark...")
    if not chosen:
        print("No candidates above the confidence floor.")
        print("Lower MIN_CONFIDENCE at the top of score_all.py and re-run,")
        print("or check data\\output\\sam_all.json - most tiles are empty,")
        print("which is normal: 0 boxes on an empty tile is a valid result.")
        sys.exit(0)

    key = get_key()
    CAND_DIR.mkdir(parents=True, exist_ok=True)

    ranked = []
    for i, cand in enumerate(chosen, start=1):
        tile_path = Path(cand["tile_path"])
        img = Image.open(tile_path).convert("RGB")
        x1, y1, x2, y2 = cand["box"]
        crop = img.crop((int(x1), int(y1), int(x2), int(y2)))
        cand_file = CAND_DIR / f"candidate-{i:02d}.jpg"
        crop.save(cand_file)
        photo_name = source_photo_for(tile_path.name)
        lat, lon = gps_for(manifest, photo_name)
        print(f"[{i}/{len(chosen)}] {tile_path.name} #{cand['object_id']} "
              f"SAM {cand['confidence']:.2f} -> {cand_file.name} ...", flush=True)
        try:
            reply = verify_with_muse(key, cand_file)
        except Exception as exc:  # noqa: BLE001 - one bad call must not kill the run
            print(f"  ERROR calling Muse: {exc} - saving as unscored, continuing.")
            reply = ""
        match = SCORE_RE.search(reply) if reply else None
        score = float(match.group(0)) if match else None
        print(f"  Muse score: {score}  {reply.strip()[:120]}")
        ranked.append({
            "rank": None,  # assigned after sorting by Muse score
            "candidate": cand_file.name,
            "tile": tile_path.name,
            "source_photo": photo_name,
            "lat": lat,
            "lon": lon,
            "sam_object_id": cand["object_id"],
            "box": cand["box"],
            "sam_confidence": cand["confidence"],
            "fictional_brief": FICTIONAL_BRIEF,
            "muse_score": score,
            "muse_reply": reply,
        })

    # Rank by Muse score first (unscored last), SAM confidence breaks ties.
    ranked.sort(key=lambda r: (r["muse_score"] is not None,
                               r["muse_score"] if r["muse_score"] is not None else -1,
                               r["sam_confidence"]), reverse=True)
    for i, row in enumerate(ranked, start=1):
        row["rank"] = i

    OUT_JSON.write_text(json.dumps(
        {"fictional_brief": FICTIONAL_BRIEF, "candidates": ranked}, indent=2),
        encoding="utf-8")
    with OUT_CSV.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=[
            "rank", "candidate", "tile", "source_photo", "lat", "lon",
            "sam_confidence", "muse_score", "muse_reply"])
        writer.writeheader()
        for row in ranked:
            writer.writerow({k: row[k] for k in writer.fieldnames})

    print()
    print("Ranked list (Muse score first):")
    for row in ranked:
        print(f"  #{row['rank']:>2}  Muse {row['muse_score']}  SAM {row['sam_confidence']:.2f}  "
              f"{row['candidate']}  ({row['source_photo']}, lat={row['lat']} lon={row['lon']})")
    print(f"Saved: {OUT_JSON}")
    print(f"Saved: {OUT_CSV}")
    print(f"Crops: {CAND_DIR}\\")
    print()
    print("Days 3-4 are done when this ranked list exists and the top rows")
    print("have scores with reasons that refer to what is actually in the crop.")
    print("Next is Day 5: the Run / Map / Review UI around this list.")


if __name__ == "__main__":
    main()
