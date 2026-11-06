"""Keeps Grain fully offline.

The needle package reaches the network in two places even when it's given local model files:
it loads a "base" model from its own download cache (downloading it if missing), and it pings
Hugging Face's download counter every time it loads. Grain ships its models, so both are
pointed at the bundled files and the ping is switched off. Nothing in the package is modified.
"""
import os

from .config import NEEDLE_WEIGHTS, WHISTLE_WEIGHTS

# Set before needle or huggingface_hub are imported anywhere.
os.environ["HF_HUB_OFFLINE"] = "1"  # huggingface_hub refuses network access instead of trying
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
os.environ["NEEDLE_TELEMETRY"] = "0"
os.environ["NEEDLE_WHISTLE_WEIGHTS"] = WHISTLE_WEIGHTS


def lock_down(needle):
    """Point needle's model lookups at Grain's bundled models and disable its download ping."""
    from needle.agent import fetch

    def bundled_base_weights(generation):
        return WHISTLE_WEIGHTS if generation == fetch.WHISTLE else NEEDLE_WEIGHTS

    needle._base_weights_path = bundled_base_weights
    fetch._register_download = lambda *args, **kwargs: None
