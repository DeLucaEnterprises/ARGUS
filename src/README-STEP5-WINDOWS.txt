ARGUS - Steps 5 + 6, today (Tue Oct 27, 2026) - Windows
==========================================================

You finished Days 1-2 last night: test-01.jpg (your IMG_7788, GPS
35.90585, -80.40520, 4000x2250), 15 unique tiles, pin on the map.
Working folder (the one that works - not C:\Users\markj\argus):
  C:\Users\markj\Downloads\argus-starter\argus

Today is Step 5 (SAM smoke test, one tile) then Step 6 (Muse verification,
one candidate). This pack replaces the TODO in src\sam_test.py with real,
ready code - no more copy-paste from the portal.

FILES IN THIS PACK - copy all four into that argus folder:
  src\sam_test.py     -> replaces the TODO template (same filename)
  src\verify_test.py  -> NEW, Step 6
  src\check_key.py    -> NEW, safe .env key check (never prints the key)
  README-STEP5-WINDOWS.txt -> this file

DO NOT copy your .env anywhere, and do not paste your key in chat.

STEP 0 - CHECK THE KEY (2 minutes, do this first)
-------------------------------------------------
In Command Prompt, in the argus folder, with the venv on:

  .venv\Scripts\python src\check_key.py

- "Key looks real" -> go to Step 5.
- Placeholder / no .env / does not start with LLM_ -> fix .env first:
  Open .env in Notepad, replace your-key-here with your real key, save.
  Get a key at https://dev.meta.ai/api-keys - sign in with the same
  account you used to apply for the hackathon, Create a key, name it
  ARGUS-hackathon. It is shown ONCE - copy it right then. It starts LLM_.
  While there, check Billing / Usage for the $150 hackathon credits.

STEP 5 - SAM SMOKE TEST (one tile, prompt "person")
----------------------------------------------------
  .venv\Scripts\python -m pip install openai meta-sam-parser
  .venv\Scripts\python src\sam_test.py

What it does: sends data\tiles\test-01_t0_0.jpg to SAM 3.1
(model sam-3.1, Meta Model API at https://api.meta.ai/v1) with the
prompt "person", decodes the boxes + masks with Meta's own
meta-sam-parser package, and saves:
  data\output\test-01_sam.json        boxes + masks
  data\output\test-01_sam_boxes.jpg   the tile with boxes drawn on it
  data\output\test-01_sam_raw.txt    the raw SAM output, for inspection

Success looks like: "Boxes found: N" (N is probably small - see below),
then open test-01_sam_boxes.jpg and check at least one box sits on a
real object, not the whole tile. One tile costs a fraction of a cent
(SAM 3.1 image pricing is $2.50 per 1,000 images).

IMPORTANT - 0 boxes is a VALID result: test-01_t0_0.jpg is the TOP-LEFT
tile of your drone photo. It may simply have nobody in it. Do not
rebuild anything. Instead, in sam_test.py change the TILE line at the
top to another tile (for example test-01_t1_2.jpg) and re-run. Or change
PROMPT to "car" just to prove SAM returns boxes at all. Step 6 needs a
tile that returned at least one box.

STEP 6 - MUSE VERIFICATION (one candidate)
-------------------------------------------
First, EDIT the fictional brief at the top of src\verify_test.py:
  FICTIONAL_BRIEF = "red jacket, dark pants, adult build"
Change it to your fictional test description. Fictional only - never a
real person or a real case.

Then run:
  .venv\Scripts\python src\verify_test.py

What it does: crops the highest-confidence SAM box out of the tile,
saves it as data\output\candidate-01.jpg, sends it to Muse Spark
(muse-spark-1.3, Standard) with that fictional brief, and saves the
score + reason to data\output\test-01_verify.json.

Success looks like: a score 0-1 with a reason that refers to what is
actually in the crop. If the score is nonsense, tighten the brief -
do not retune the pipeline.

IF IT ERRORS, send Roby the step number + the exact error:
  - 401 / Unauthorized ......... key is wrong or not copied fully - re-copy
    from https://dev.meta.ai/api-keys and re-save .env (key shown once).
  - 404 model_not_found ........ your team cannot access sam-3.1 yet -
    check the portal / Discord for model access and the $150 credits.
  - No module named openai / meta_sam_parser ... re-run the pip install
    line in Step 5, in the same window, with the venv on.
  - Tile not found ............. you are in the wrong argus folder - use
    C:\Users\markj\Downloads\argus-starter\argus (see top of this file).

AFTER STEPS 5-6 WORK - one small fix before scaling to a folder
---------------------------------------------------------------
ingest.py currently counts each photo twice on Windows (it globs *.jpg
AND *.JPG, and Windows treats them as the same file - that is why last
night said "2 photo(s)" / "30 tiles" for 1 photo / 15 tiles). Harmless
for one photo, wrong for a folder. Before the Day-2 folder run, change
this ONE line in src\ingest.py:

  FROM:
    for path in sorted(INPUT_DIR.glob("*.jpg")) + sorted(INPUT_DIR.glob("*.JPG")) + sorted(INPUT_DIR.glob("*.jpeg")) + sorted(INPUT_DIR.glob("*.JPEG")):
  TO:
    seen = set()
    for path in sorted(INPUT_DIR.glob("*.jpg")) + sorted(INPUT_DIR.glob("*.jpeg")):
        key = str(path.resolve()).lower()
        if key in seen:
            continue
        seen.add(key)

Then re-run setup-and-run.bat. The manifest should then say 1 photo.

WHAT IS STILL ONLY YOURS TO DO
-------------------------------
- The key itself (only you can create/copy it - it is shown once).
- The fictional brief wording in verify_test.py.
- Which tile to try if the first one returns 0 boxes.
