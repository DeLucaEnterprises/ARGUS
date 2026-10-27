# ARGUS - .env key check (Windows)
#
# Safe check: tells you whether .env has a real-looking Meta key.
# It NEVER prints the key - only its length and whether it starts with LLM_.
#
# Run from the argus folder:
#   .venv\Scripts\python src\check_key.py
# (or plain: python src\check_key.py)
#
# Exit 0 = key looks real. Exit 1 = fix .env first (see the message).

import sys
from pathlib import Path


def load_env(path=".env"):
    env = {}
    p = Path(path)
    if not p.exists():
        return env, False
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env, True


def main():
    env, exists = load_env()
    if not exists:
        print("No .env file in this folder.")
        print("Create it in the argus folder with one line: META_API_KEY=your-key")
        sys.exit(1)
    key = env.get("META_API_KEY", "")
    if not key:
        print(".env exists but has no META_API_KEY line.")
        sys.exit(1)
    if key == "your-key-here":
        print(".env still has the placeholder your-key-here - replace it with your real key.")
        print("Get one at: https://dev.meta.ai/api-keys (Create a key, copy it once.)")
        sys.exit(1)
    print(f"Key found in .env: {len(key)} characters, starts with LLM_: {key.startswith('LLM_')}")
    if not key.startswith("LLM_"):
        print("WARNING: Meta Model API keys start with LLM_ - double-check you copied the right key.")
        sys.exit(1)
    print("Key looks real. You are ready for Step 5.")
    sys.exit(0)


if __name__ == "__main__":
    main()
