"""Fetch the exact model files Grain ships, and refuse anything else.

Each model is pinned to a Hugging Face commit and checked against its SHA-256, so a new upload to
either repository can never slip into a build unnoticed. Files already in models/ that match are
left alone, so this also verifies a local copy without downloading.

    python dev/fetch_models.py           download what's missing, verify everything
    python dev/fetch_models.py --check   verify only; never touches the network

To move to a newer model, update the commit and SHA-256 here and in models/README.txt together.
"""

import argparse
import hashlib
import os
import sys

MODELS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models")

PINNED = [
    # (repository, file, commit, sha256)
    ("Cactus-Compute/needle3", "needle3.cact", "c95d25b4efa1e9601e785d92438e4ade0dbddbf8",
     "c9d915eca282ed42d1a09b143b592adb4cc6744ffe2d294adf5cfc5548170c38"),
    ("Cactus-Compute/whistle", "whistle.cact", "ad73f1468dd5203791e0cc81d31544c553563645",
     "b6e02f048568ac5d01a2042556c658061e699acbc0aa2a1439f52f3d461dffeb"),
]


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="only verify the files already in models/")
    args = parser.parse_args()

    failed = False
    for repo, filename, commit, expected in PINNED:
        path = os.path.join(MODELS_DIR, filename)
        if os.path.exists(path) and sha256(path) == expected:
            print(f"ok       {filename} ({expected[:12]})")
            continue
        if args.check:
            print(f"MISSING  {filename}" if not os.path.exists(path) else f"MISMATCH {filename}")
            failed = True
            continue
        if os.path.lexists(path):
            os.remove(path)  # a stale or different file: replace it with the pinned one
        from huggingface_hub import hf_hub_download
        hf_hub_download(repo_id=repo, filename=filename, revision=commit, local_dir=MODELS_DIR)
        actual = sha256(path)
        if actual != expected:
            os.remove(path)
            print(f"MISMATCH {filename}: got {actual}, expected {expected} (from {repo}@{commit[:12]})")
            failed = True
        else:
            print(f"fetched  {filename} ({expected[:12]}) from {repo}@{commit[:12]}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
