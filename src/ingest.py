import json
from pathlib import Path
from PIL import Image
from PIL.ExifTags import TAGS, GPSTAGS

INPUT_DIR = Path("data/input")
OUTPUT_FILE = Path("data/output/manifest.json")
GPS_IFD = 0x8825

def dms_to_decimal(dms, ref):
    d, m, s = [float(x) for x in dms]
    val = d + m / 60 + s / 3600
    return -val if ref in ("S", "W") else val

def read_gps(path):
    img = Image.open(path)
    exif = img.getexif()
    # GPS data lives in its own IFD (0x8825). getexif()["GPSInfo"] is just
    # an int offset in Pillow, not the GPS dict itself — use get_ifd().
    try:
        gps_raw = exif.get_ifd(GPS_IFD)
    except Exception:
        gps_raw = {}
    if not gps_raw:
        return None, None, img.size
    gps = {GPSTAGS.get(k, k): v for k, v in gps_raw.items()}
    if "GPSLatitude" not in gps or "GPSLongitude" not in gps:
        return None, None, img.size
    lat = dms_to_decimal(gps["GPSLatitude"], gps["GPSLatitudeRef"])
    lon = dms_to_decimal(gps["GPSLongitude"], gps["GPSLongitudeRef"])
    return lat, lon, img.size

def main():
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    photos = []
    for path in sorted(INPUT_DIR.glob("*.jpg")) + sorted(INPUT_DIR.glob("*.JPG")) + sorted(INPUT_DIR.glob("*.jpeg")) + sorted(INPUT_DIR.glob("*.JPEG")):
        lat, lon, (w, h) = read_gps(path)
        photos.append({
            "filename": path.name,
            "path": str(path),
            "lat": lat,
            "lon": lon,
            "width": w,
            "height": h,
        })
        print(f"{path.name}: lat={lat} lon={lon} {w}x{h}")
    OUTPUT_FILE.write_text(json.dumps({"photos": photos}, indent=2))
    print(f"Wrote {OUTPUT_FILE} with {len(photos)} photo(s)")

if __name__ == "__main__":
    main()
