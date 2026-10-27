# ARGUS - Step 5 SAM smoke test (Windows)
# Replaces the TODO template in src\sam_test.py
#
# What this does, in order:
#   1. Reads META_API_KEY from .env in the argus folder (never prints it).
#   2. Sends ONE tile to SAM 3.1 on the Meta Model API with the text prompt "person".
#   3. Saves boxes + masks to data\output\test-01_sam.json
#      and a copy of the tile with the boxes drawn on it to
#      data\output\test-01_sam_boxes.jpg
#
# How it calls SAM 3.1 (verified 2026-10-27 against Meta's published shape):
#   - OpenAI-compatible client pointed at base_url "https://api.meta.ai/v1"
#   - Responses API: client.responses.create(model="sam-3.1", ...)
#   - One short concept phrase per request: "person"
#   - The streamed output text is decoded with Meta's own parser package,
#     meta-sam-parser (image_segmentation_format), which turns the wire
#     format into box + mask records. Do NOT hand-parse the raw text.
#   - Pricing on Meta's SAM 3.1 page: $2.50 per 1,000 images, so one tile
#     costs a fraction of a cent. The $150 hackathon credits cover it.
#
# If the portal Quickstart shows a different image field than input_image
# below, use the portal's field - the rest of this file (key loading,
# saving, box drawing) stays the same. Paste the portal example's exact
# request shape in that case and tell Roby what it showed.
#
# To try a different tile, change TILE below to another file in data\tiles\
# (15 tiles for test-01.jpg: rows t0-t2, cols t0-t4, e.g. test-01_t1_2.jpg).
# To try a different concept, change PROMPT below - one short noun phrase.
#
# Run from the argus folder, with the venv on:
#   .venv\Scripts\python -m pip install openai meta-sam-parser
#   .venv\Scripts\python src\sam_test.py

import asyncio
import base64
import json
import os
import sys
from pathlib import Path

from PIL import Image, ImageDraw

TILE = "data/tiles/test-01_t1_2.jpg"
PROMPT = "person"
OUT_JSON = "data/output/test-01_sam.json"
OUT_RAW = "data/output/test-01_sam_raw.txt"
OUT_BOXES = "data/output/test-01_sam_boxes.jpg"


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
    env = load_env()
    key = env.get("META_API_KEY") or os.environ.get("META_API_KEY", "")
    if not key or key == "your-key-here":
        print("ERROR: no Meta key in .env yet.")
        print("  Open .env in Notepad and replace your-key-here with your real key.")
        print("  Get one at: https://dev.meta.ai/api-keys  (Create a key, copy it once.)")
        print("  Do not paste the key in chat.")
        sys.exit(2)
    if not key.startswith("LLM_"):
        print("WARNING: your key does not start with LLM_ - it may be the wrong key.")
        print("  Meta Model API keys start with LLM_. Check .env and re-copy from")
        print("  https://dev.meta.ai/api-keys if needed. Continuing anyway...")
    return key


def data_url(path):
    raw = Path(path).read_bytes()
    return "data:image/jpeg;base64," + base64.b64encode(raw).decode("ascii")


def build_detections(result, tile_path):
    """Turn parsed SAM records into plain JSON-safe detections.

    The parser returns, per object id, one SegmentationBoxRecord
    (left/top/right/bottom, confidence) and one SegmentationMaskRecord
    (mask.encoding/payload/width/height, bounds). Pair them by object_id.
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
        print("SAM returned an incomplete outcome or diagnostics:")
        print("  outcome:", result.outcome)
        print("  diagnostics:", result.diagnostics)
        print("  raw output saved to:", OUT_RAW)
        Path(OUT_RAW).write_text(result.raw_output or "", encoding="utf-8")
        sys.exit(3)
    return result


def main():
    tile_path = Path(TILE)
    if not tile_path.exists():
        print(f"ERROR: tile not found: {tile_path}")
        print("  Run setup-and-run.bat first, then check data\\tiles\\ for the tile names.")
        print("  Change TILE at the top of this file to one that exists.")
        sys.exit(2)

    key = get_key()
    print(f"Tile: {tile_path}  Prompt: \"{PROMPT}\"")
    print("Calling SAM 3.1... (one tile, a few seconds)")
    result = asyncio.run(run_sam(key, tile_path))

    payload = build_detections(result, tile_path)
    Path(OUT_JSON).parent.mkdir(parents=True, exist_ok=True)
    Path(OUT_JSON).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    Path(OUT_RAW).write_text(result.raw_output or "", encoding="utf-8")
    draw_boxes(tile_path, payload, OUT_BOXES)

    print(f"Boxes found: {payload['count']}")
    for det in payload["detections"]:
        print(f"  #{det['object_id']}: box={det['box']} confidence={det['confidence']}")
    print(f"Saved: {OUT_JSON}")
    print(f"Saved: {OUT_BOXES}  (open this to eyeball the boxes)")
    print(f"Raw SAM output saved to: {OUT_RAW}")
    if payload["count"] == 0:
        print()
        print("0 boxes is a VALID result - it means SAM found no person in this tile.")
        print("This tile is the top-left corner of the photo; it may simply have")
        print("nobody in it. Do not rebuild anything. Instead, try another tile:")
        print("  change TILE at the top of this file to test-01_t1_2.jpg and re-run,")
        print("  or try PROMPT = \"car\" just to prove SAM returns boxes at all.")
        print("Step 6 needs a tile that returned at least one box.")


if __name__ == "__main__":
    main()
