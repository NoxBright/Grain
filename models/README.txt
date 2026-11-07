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
Keep their checksums with each release and distribute them through the release artifact or an
approved model-download process. Grain itself disables model downloads and telemetry at runtime.
