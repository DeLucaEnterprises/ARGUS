# ARGUS - Step 6 Muse verification smoke test (Windows)
#
# Run AFTER Step 5 works, on a tile that returned at least one box.
#
# What this does, in order:
#   1. Reads data\output\test-01_sam.json, takes the highest-confidence box.
#   2. Crops that box out of the tile and saves it as
#      data\output\candidate-01.jpg
#   3. Sends the crop to Muse Spark (muse-spark-1.3, Standard - NOT the
#      -contributor version) with the fictional case brief below, and asks
#      for a score 0-1 plus one sentence why.
#   4. Saves the reply to data\output\test-01_verify.json
#
# EDIT THE BRIEF FIRST: replace FICTIONAL_BRIEF with your fictional test
# description (clothing colours, build). Fictional only - never a real
# person or a real case. The default below is the placeholder from the
# Step 5 instructions; change it to whatever your fictional case is.
#
# Run from the argus folder, with the venv on:
#   .venv\Scripts\python src\verify_test.py

import base64
import json
import re
import sys
from pathlib import Path

from PIL import Image

SAM_JSON = "data/output/test-01_sam.json"
CANDIDATE = "data/output/candidate-01.jpg"
OUT_JSON = "data/output/test-01_verify.json"

# --- EDIT THIS: fictional test case only, never a real person/case ---
FICTIONAL_BRIEF = "red jacket, dark pants, adult build"


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


def crop_top_candidate():
    sam_path = Path(SAM_JSON)
    if not sam_path.exists():
        print(f"ERROR: {SAM_JSON} not found. Run Step 5 (sam_test.py) first.")
        sys.exit(2)
    payload = json.loads(sam_path.read_text(encoding="utf-8"))
    detections = [d for d in payload.get("detections", []) if d.get("box")]
    if not detections:
        print("No boxes in test-01_sam.json - nothing to verify.")
        print("Re-run Step 5 on a different tile (see sam_test.py TILE line).")
        sys.exit(2)
    # Highest confidence first; None confidence sorts last.
    detections.sort(key=lambda d: (d.get("confidence") is not None, d.get("confidence") or 0),
                    reverse=True)
    top = detections[0]
    tile_path = Path(payload["tile"])
    img = Image.open(tile_path).convert("RGB")
    x1, y1, x2, y2 = top["box"]
    crop = img.crop((int(x1), int(y1), int(x2), int(y2)))
    Path(CANDIDATE).parent.mkdir(parents=True, exist_ok=True)
    crop.save(CANDIDATE)
    print(f"Top candidate: #{top['object_id']} box={top['box']} "
          f"confidence={top.get('confidence')}")
    print(f"Saved crop: {CANDIDATE} ({crop.size[0]}x{crop.size[1]})")
    return top


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
    top = crop_top_candidate()
    key = get_key()
    print("Sending crop to Muse Spark for verification...")
    reply = verify_with_muse(key, CANDIDATE)
    print("Muse reply:")
    print(" ", reply)
    match = re.search(r"0(?:\.\d+)?|1(?:\.0+)?", reply)
    result = {
        "candidate": CANDIDATE,
        "box": top["box"],
        "confidence_sam": top.get("confidence"),
        "fictional_brief": FICTIONAL_BRIEF,
        "muse_reply": reply,
        "score_extracted": float(match.group(0)) if match else None,
    }
    Path(OUT_JSON).write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"Saved: {OUT_JSON}")
    print()
    print("Check: the score should come with a reason that refers to what is")
    print("actually in the crop. If the score is nonsense, tighten the fictional")
    print("brief - do not retune the pipeline.")


if __name__ == "__main__":
    main()
