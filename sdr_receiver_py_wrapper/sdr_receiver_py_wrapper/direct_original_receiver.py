from __future__ import annotations

import argparse
import os

from .original_receiver_adapter import load_original_module


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the original SDR receiver script directly, without ROS2 or monkey patches."
    )
    parser.add_argument(
        "--script-path",
        default=os.environ.get("SDR_RECEIVER_ORIGINAL_SCRIPT", "auto"),
        help="Original v67 receiver script path, or 'auto' to use the bundled fallback.",
    )
    args = parser.parse_args()

    module = load_original_module(args.script_path)
    print(f"[direct_original_receiver] loaded: {module.__file__}")
    print("[direct_original_receiver] running original main() without ROS2 wrapper patches")
    module.main()


if __name__ == "__main__":
    main()
