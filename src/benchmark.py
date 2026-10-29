# ARGUS - Day 6: benchmark scoreboard (Windows)
#
# What this does, and what it deliberately does NOT do:
#   It scores a test run AFTER Days 3-4 (and Day 5 review, if done).
#   It does NOT generate synthetic subjects, does NOT call any API,
#   and needs NO key. The test set is yours to make, honestly:
#     - Day 7 volunteers in known positions (you know where they stood),
#     - or Muse Image synthetic subjects composited into real search
#       imagery (you generate those images yourself, in the portal
#       or with Muse Image - this script does not guess that API).
#   You write the ground truth by hand in benchmark_truth.csv.
#   This script only checks the ranked list against that truth and
#   computes the recall score you can publish.
#
# Metric, stated plainly so the published number is honest:
#   PHOTO-LEVEL recall, not pixel-perfect box recall. A planted-
#   subject photo counts as FOUND if the ranked list contains at
#   least one candidate from that photo at or above the Muse
#   threshold. That matches what ARGUS promises a search team:
#   the photo gets surfaced for a human to verify. It does NOT
#   claim the box was exact, and it does NOT claim anything about
#   real cases - test subjects only, fictional brief only.
#
# Inputs (run from the argus folder):
#   data/output/ranked_candidates.json   (Days 3-4 output; the
#       Day 5 review_agent.json "kept" list is also accepted - pass
#       it with --ranked if you want the deduped list scored)
#   data/benchmark_truth.csv             (you fill this in; see the
#       template in this pack. One row per test photo.)
#
# Truth CSV columns (header row required):
#   source_photo,subject_present,brief,notes
#     source_photo: filename exactly as in ranked_candidates.json,
#       e.g. test-01.jpg or frame_0003.jpg
#     subject_present: yes / no (also accepts 1 / 0 / true / false)
#     brief: the fictional brief for that subject, e.g.
#       "red jacket, dark pants, adult build" (or "none" for
#       no-subject photos)
#     notes: free text - where the subject stood, anything a
#       reviewer should know. Optional but recommended.
#
# Outputs (it never overwrites ranked_candidates.json):
#   data/output/benchmark_results.json - full per-photo breakdown
#   data/output/benchmark_scoreboard.md - the publishable scoreboard
#
# Run from the argus folder:
#   .venv\Scripts\python src\benchmark.py
#   .venv\Scripts\python src\benchmark.py --ranked data\output\review_agent.json
#
# Headline threshold is Muse >= 0.50. The scoreboard also prints
# recall at 0.40 and 0.60, so one threshold choice cannot hide
# a weak result. Unscored candidates (Muse score missing) never
# count as found.

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_RANKED = Path("data/output/ranked_candidates.json")
TRUTH = Path("data/benchmark_truth.csv")
OUT_JSON = Path("data/output/benchmark_results.json")
OUT_MD = Path("data/output/benchmark_scoreboard.md")
THRESHOLDS = (0.40, 0.50, 0.60)
HEADLINE = 0.50

YES = {"yes", "y", "1", "true"}
NO = {"no", "n", "0", "false"}


def score_of(cand):
    """Review score when Day 5 produced one, else the Muse score."""
    for key in ("review_score", "muse_score"):
        value = cand.get(key)
        if value is not None:
            return value
    return None


def load_candidates(path):
    if not path.exists():
        print(f"ERROR: {path} not found.")
        print("Run Days 3-4 first: .venv\\Scripts\\python src\\score_all.py")
        sys.exit(2)
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "candidates" in data:
        candidates = data["candidates"]
    elif isinstance(data, dict) and "kept" in data:
        candidates = data["kept"]
        print("Scoring the Day 5 Review Agent kept list "
              f"({len(candidates)} candidates).")
    else:
        print(f"ERROR: {path} has neither 'candidates' nor 'kept'.")
        sys.exit(2)
    return candidates


def load_truth(path):
    if not path.exists():
        print(f"ERROR: {path} not found.")
        print("Copy benchmark_truth_template.csv from this pack to")
        print("data\\benchmark_truth.csv and fill in one row per test")
        print("photo. Do not guess rows - only photos you know the")
        print("ground truth for go in this file.")
        sys.exit(2)
    rows = []
    seen = set()
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames or "source_photo" not in reader.fieldnames \
                or "subject_present" not in reader.fieldnames:
            print("ERROR: truth CSV needs at least source_photo and "
                  "subject_present columns.")
            sys.exit(2)
        for line in reader:
            photo = (line.get("source_photo") or "").strip()
            raw = (line.get("subject_present") or "").strip().lower()
            if not photo:
                continue
            if photo in seen:
                print(f"WARNING: duplicate truth row for {photo} - "
                      "keeping the first, skipping this one.")
                continue
            seen.add(photo)
            if raw in YES:
                present = True
            elif raw in NO:
                present = False
            else:
                print(f"ERROR: {photo} has subject_present={raw!r} - "
                      "use yes or no.")
                sys.exit(2)
            rows.append({
                "source_photo": photo,
                "subject_present": present,
                "brief": (line.get("brief") or "").strip(),
                "notes": (line.get("notes") or "").strip(),
            })
    if not rows:
        print("ERROR: truth file has no photo rows.")
        sys.exit(2)
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ranked", default=str(DEFAULT_RANKED),
                        help="ranked_candidates.json or review_agent.json")
    args = parser.parse_args()

    candidates = load_candidates(Path(args.ranked))
    truth = load_truth(TRUTH)

    # Best score per source photo, and the candidate that gave it.
    best = {}
    for cand in candidates:
        photo = cand.get("source_photo")
        if not photo:
            continue
        value = score_of(cand)
        if value is None:
            continue
        if photo not in best or value > best[photo]["score"]:
            best[photo] = {"score": value, "candidate": cand.get("candidate"),
                           "tile": cand.get("tile"),
                           "sam_confidence": cand.get("sam_confidence")}

    per_photo = []
    for row in truth:
        hit = best.get(row["source_photo"])
        entry = dict(row)
        entry["best_score"] = hit["score"] if hit else None
        entry["best_candidate"] = hit["candidate"] if hit else None
        entry["best_tile"] = hit["tile"] if hit else None
        entry["found"] = {t: (hit is not None and hit["score"] >= t)
                          for t in THRESHOLDS}
        per_photo.append(entry)

    planted = [p for p in per_photo if p["subject_present"]]
    empty = [p for p in per_photo if not p["subject_present"]]
    results = {}
    for t in THRESHOLDS:
        found = sum(1 for p in planted if p["found"][t])
        recall = (found / len(planted)) if planted else None
        false_alarms = sum(1 for p in empty if p["found"][t])
        results[str(t)] = {
            "planted_photos": len(planted),
            "found": found,
            "missed": len(planted) - found,
            "recall": recall,
            "no_subject_photos": len(empty),
            "false_alarms": false_alarms,
        }

    headline = results[str(HEADLINE)]
    missed = [p for p in planted if not p["found"][HEADLINE]]
    false_alarm_rows = [p for p in empty if p["found"][HEADLINE]]

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ranked_file": str(args.ranked),
        "candidates_scored": len(candidates),
        "headline_threshold": HEADLINE,
        "results_by_threshold": results,
        "per_photo": per_photo,
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(out, indent=2), encoding="utf-8")

    lines = [
        "# ARGUS benchmark scoreboard",
        "",
        f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} - "
        "test subjects only, fictional brief only. Not a real-case claim.",
        "",
        f"**Headline (Muse >= {HEADLINE}): recall "
        f"{headline['found']}/{headline['planted_photos']}",
    ]
    if headline["recall"] is not None:
        lines[-1] += f" = {headline['recall']:.0%}"
    lines[-1] += "** - photo-level: a planted-subject photo counts as found "
    lines[-1] += "when the ranked list surfaces at least one candidate from it."
    lines += [
        "",
        "| Muse threshold | Found | Missed | Recall | False alarms (no-subject photos flagged) |",
        "|---|---|---|---|---|",
    ]
    for t in THRESHOLDS:
        r = results[str(t)]
        recall = f"{r['recall']:.0%}" if r["recall"] is not None else "n/a"
        lines.append(f"| >= {t:.2f} | {r['found']} | {r['missed']} | {recall} | "
                     f"{r['false_alarms']} of {r['no_subject_photos']} |")
    lines += [
        "",
        f"Test photos: {len(per_photo)} ({len(planted)} with a subject, "
        f"{len(empty)} without). Candidates in ranked list: {len(candidates)}.",
        "",
    ]
    if missed:
        lines += ["## Missed at the headline threshold (look at these first)", ""]
        for p in missed:
            lines.append(f"- {p['source_photo']} - best Muse "
                         f"{p['best_score']} via {p['best_candidate'] or 'none'} "
                         f"({p['best_tile'] or 'no candidate'}) - "
                         f"brief: {p['brief']} - {p['notes']}")
        lines.append("")
    else:
        lines += ["## Missed at the headline threshold", "", "- None.", ""]
    if false_alarm_rows:
        lines += ["## False alarms at the headline threshold", ""]
        for p in false_alarm_rows:
            lines.append(f"- {p['source_photo']} - best Muse {p['best_score']} "
                         f"via {p['best_candidate']} - {p['notes']}")
        lines.append("")
    lines += [
        "## How to read this honestly",
        "",
        "- Small test sets swing wildly: 1 miss in 10 photos is 10 points "
        "of recall. Publish the fraction (e.g. 8/10), not just the %.",
        "- Photo-level recall is the claim. It does not say the box was "
        "exact, and it says nothing about real cases.",
        "- If a miss is a volunteer standing in plain view, that is the "
        "finding - fix the pipeline, do not drop the photo from truth.",
        "",
    ]
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")

    print()
    print("ARGUS benchmark:")
    for t in THRESHOLDS:
        r = results[str(t)]
        recall = f"{r['recall']:.0%}" if r["recall"] is not None else "n/a"
        print(f"  Muse >= {t:.2f}: {r['found']}/{r['planted_photos']} "
              f"recall {recall}, false alarms {r['false_alarms']}")
    print(f"Saved: {OUT_JSON}")
    print(f"Saved: {OUT_MD}")
    if missed:
        print(f"Missed at {HEADLINE}: " + ", ".join(p["source_photo"] for p in missed))


if __name__ == "__main__":
    main()
