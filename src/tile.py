import json
from pathlib import Path
from PIL import Image

TILE = 1024
OVERLAP = 128
STEP = TILE - OVERLAP

def main():
    manifest = json.loads(Path("data/output/manifest.json").read_text())
    tiles_out = []
    for photo in manifest["photos"]:
        img = Image.open(photo["path"])
        w, h = img.size
        row = 0
        y = 0
        while y < h:
            col = 0
            x = 0
            while x < w:
                box = (x, y, min(x + TILE, w), min(y + TILE, h))
                tile = img.crop(box)
                name = f"{Path(photo['filename']).stem}_t{row}_{col}.jpg"
                tile.save(Path("data/tiles") / name)
                tiles_out.append({
                    "tile": name,
                    "source": photo["filename"],
                    "row": row, "col": col,
                    "x": x, "y": y,
                    "width": box[2] - box[0],
                    "height": box[3] - box[1],
                })
                x += STEP
                col += 1
            y += STEP
            row += 1
    Path("data/output/tiles.json").write_text(json.dumps({"tiles": tiles_out}, indent=2))
    print(f"Wrote {len(tiles_out)} tiles to data/tiles/")

if __name__ == "__main__":
    main()
