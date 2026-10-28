# ARGUS - Days 3-4, Part 1: SAM scan across ALL tiles (Windows)
#
# Run AFTER Day 2 is complete (folder ingest + tiling + map working).
#
# What this does, in order:
#   1. Reads META_API_KEY from .env in the argus folder (never prints it).
#   2. Finds every tile in data\tiles\ (all photos, all video frames).
#   3. Sends each tile to SAM 3.1 on the Meta Model API with the text
#      prompt "person" - the exact same call as the Step 5 smoke test
#      (sam_test.py), just looped over the whole folder.
#   4. Saves, per tile, data\output\sam\<tile>_sam.json and a boxes-drawn
#      copy data\output\sam\<tile>_boxes.jpg, plus one combined file
#      data\output\sam_all.json with every tile's detections.
#   5. Skips tiles already scanned (resume-safe): re-running this file
#      only scans tiles with no saved result yet, so an interrupted run
#      or a 401/rate-limit stop costs nothing to restart.
#
# Cost: SAM 3.1 image pricing is $2.50 per 1,000 images, so one tile
# costs a fraction of a cent. A folder of ~50-75 tiles is well under
# a dollar. Check Billing at https://dev.meta.ai first if the $150
# hackathon credits claim is still unconfirmed - see README.
#
# 0 boxes on a tile is a VALID result - it means SAM found no person
# in that tile. Empty tiles are expected; most tiles have nobody in them.
#
# Run from the argus folder, with the venv on:
#   .venv\Scripts\python src\scan_all.py
#
# Then Part 2 (score_all.py) Muse-scores the top candidates.

import asyncio
import base64
import json
import os
import sys
from pathlib import Path

from PIL import Image, ImageDraw

TILES_DIR = Path("data/tiles")
OUT_DIR = Path("data/output/sam")
COMBINED = Path("data/output/sam_all.json")
PROMPT = "person"


def load_env(path=".env"):
    """Read KEY=VALUE lines from .env. No extra dependency needed."""
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
    key = load_env().get("META_API_KEY") or os.environ.get("META_API_KEY", "")
    if not key or key == "your-key-here":
        print("ERROR: no Meta key in .env yet.")
        print("  Open .env in Notepad and replace your-key-here with your real key.")
        print("  Get one at: https://dev.meta.ai/api-keys  (Create a key, copy it once.)")
        print("  Do not paste the key in chat.")
        sys.exit(2)
    if not key.startswith("LLM_"):
        print("WARNING: your key does not start with LLM_ - it may be the wrong key.")
        print("  Check .env and re-copy from https://dev.meta.ai/api-keys if needed.")
        print("  Continuing anyway...")
    return key


def data_url(path):
    raw = Path(path).read_bytes()
    return "data:image/jpeg;base64," + base64.b64encode(raw).decode("ascii")


def build_detections(result, tile_path):
    """Turn parsed SAM records into plain JSON-safe detections.

    Same shape as Step 5 (sam_test.py): per object id, one
    SegmentationBoxRecord (left/top/right/bottom, confidence) paired
    with one SegmentationMaskRecord by object_id.
    """
    from meta_sam_parser import SegmentationBoxRecord, SegmentationMaskRecord

    img = Image.open(tile_path)
    width, height = img.size
    boxes = {}
    masks = {}
    for record in result.records:
        if isinstance(record, SegmentationBoxRecord):
            boxes[record.object_id] = record
        elif isinstance(record, SegmentationMaskRecord):
            masks[record.object_id] = record

    detections = []
    for object_id in sorted(set(boxes) | set(masks), key=lambda x: str(x)):
        box = boxes.get(object_id)
        mask = masks.get(object_id)
        detections.append({
            "object_id": str(object_id),
            "box": [box.left, box.top, box.right, box.bottom] if box else None,
            "confidence": (
                box.confidence if box is not None and box.confidence is not None
                else (mask.confidence if mask is not None else None)
            ),
            "mask": (
                {
                    "width": mask.mask.width,
                    "height": mask.mask.height,
                    "encoding": mask.mask.encoding,
                    "payload": mask.mask.payload,
                } if mask is not None else None
            ),
        })
    return {
        "prompt": PROMPT,
        "tile": str(tile_path),
        "tile_size": [width, height],
        "count": len(detections),
        "detections": detections,
    }


def draw_boxes(tile_path, payload, out_path):
    img = Image.open(tile_path).convert("RGB")
    draw = ImageDraw.Draw(img)
    for det in payload["detections"]:
        if not det["box"]:
            continue
        x1, y1, x2, y2 = det["box"]
        draw.rectangle([x1, y1, x2, y2], outline=(255, 0, 0), width=4)
        label = f"#{det['object_id']}"
        if det["confidence"] is not None:
            label += f" {det['confidence']:.2f}"
        draw.text((x1 + 4, y1 + 4), label, fill=(255, 0, 0))
    img.save(out_path)
    return out_path


async def run_sam(key, tile_path):
    from openai import AsyncOpenAI
    from meta_sam_parser import (
        CompletedOutcome,
        image_segmentation_format,
        parse_responses_stream,
    )

    async with AsyncOpenAI(
        base_url="https://api.meta.ai/v1",
        api_key=key,
    ) as client:
        events = await client.responses.create(
            model="sam-3.1",
            input=[{
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": PROMPT},
                    {"type": "input_image", "image_url": data_url(tile_path)},
                ],
            }],
            stream=True,
            metadata={"mask_encoding": "one_bit", "include_confidence": "true"},
        )
        parsed = parse_responses_stream(events, image_segmentation_format())
        async with parsed:
            result = await parsed.final_result()

    if not isinstance(result.outcome, CompletedOutcome) or result.diagnostics:
        return None, result
    return result, None


def main():
    tiles = sorted(TILES_DIR.glob("*.jpg")) + sorted(TILES_DIR.glob("*.jpeg"))
    # Windows case-insensitivity: same dedupe as ingest.py Day 2 fix.
    seen = set()
    unique = []
    for path in tiles:
        key = str(path.resolve()).lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    tiles = unique

    if not tiles:
        print(f"ERROR: no tiles in {TILES_DIR}. Run setup-and-run.bat first.")
        sys.exit(2)

    key = get_key()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Resume: load any already-scanned tiles from the combined file.
    combined = {}
    if COMBINED.exists():
        try:
            combined = json.loads(COMBINED.read_text(encoding="utf-8"))
        except Exception:
            combined = {}
    scanned = combined.get("tiles", {})

    print(f"{len(tiles)} tile(s) in {TILES_DIR}, prompt \"{PROMPT}\"")
    print(f"{len(scanned)} already scanned - those will be skipped.")
    print("Calling SAM 3.1, one tile at a time...")

    total_boxes = 0
    newly_scanned = 0
    for i, tile_path in enumerate(tiles, start=1):
        stem = tile_path.stem
        out_json = OUT_DIR / f"{stem}_sam.json"
        if stem in scanned and out_json.exists():
            total_boxes += scanned[stem].get("count", 0)
            continue
        print(f"[{i}/{len(tiles)}] {tile_path.name} ...", flush=True)
        try:
            result, bad = asyncio.run(run_sam(key, tile_path))
        except Exception as exc:  # noqa: BLE001 - one bad tile must not kill the run
            print(f"  ERROR calling SAM: {exc}")
            print("  Stopping here - re-run this file later to resume from this tile.")
            break
        if bad is not None:
            print("  SAM returned an incomplete outcome or diagnostics - skipping tile.")
            print(f"  outcome: {bad.outcome}  diagnostics: {bad.diagnostics}")
            raw_path = OUT_DIR / f"{stem}_sam_raw.txt"
            raw_path.write_text(bad.raw_output or "", encoding="utf-8")
            continue
        payload = build_detections(result, tile_path)
        out_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        draw_boxes(tile_path, payload, OUT_DIR / f"{stem}_boxes.jpg")
        scanned[stem] = payload
        total_boxes += payload["count"]
        newly_scanned += 1
        print(f"  Boxes found: {payload['count']}")

    combined = {"prompt": PROMPT, "tiles_scanned": len(scanned),
                "total_boxes": total_boxes, "tiles": scanned}
    COMBINED.write_text(json.dumps(combined, indent=2), encoding="utf-8")

    print()
    print(f"Tiles scanned: {len(scanned)}  (new this run: {newly_scanned})")
    print(f"Total SAM boxes: {total_boxes}")
    print(f"Combined results: {COMBINED}")
    print(f"Per-tile results: {OUT_DIR}\\")
    if len(scanned) < len(tiles):
        print()
        print("Not all tiles scanned yet - re-run this file to resume.")
        print("It skips tiles that already have a saved result.")
    else:
        print()
        print("Scan complete. Next: Part 2 - Muse-score the top candidates:")
        print("  .venv\\Scripts\\python src\\score_all.py")


if __name__ == "__main__":
    main()
