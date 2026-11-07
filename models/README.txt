Grain model files
=================

This directory must contain the locally bundled model files before an offline build or model
smoke test can run:

  needle3.cact
  whistle.cact

The models are Cactus Compute's Needle 3 and Whistle models. They are distributed under the
Apache License 2.0. The exact licence copies used by the build are in dev/licenses-src/ and are
included in the generated third-party notices.

The model binaries are intentionally ignored by Git because they are large release artifacts.
Every build ships these exact files, pinned to a Hugging Face commit and checked by SHA-256:

  needle3.cact  Cactus-Compute/needle3 @ c95d25b4efa1e9601e785d92438e4ade0dbddbf8
                sha256 c9d915eca282ed42d1a09b143b592adb4cc6744ffe2d294adf5cfc5548170c38
  whistle.cact  Cactus-Compute/whistle @ ad73f1468dd5203791e0cc81d31544c553563645
                sha256 b6e02f048568ac5d01a2042556c658061e699acbc0aa2a1439f52f3d461dffeb

Fetch or verify them with:

  python dev/fetch_models.py           download what's missing, verify everything
  python dev/fetch_models.py --check   verify only, no network

build.ps1 refuses to build unless both files match. To move to a newer model, change the commit
and SHA-256 in dev/fetch_models.py and here together. Grain itself disables model downloads and
telemetry at runtime.
