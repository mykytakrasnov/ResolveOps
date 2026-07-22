#!/usr/bin/env python3
"""Generate the deterministic AtlasFlow v1 fixture dataset."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PYTHON_SOURCE = REPOSITORY_ROOT / "services/agent-api/src"
sys.path.insert(0, str(PYTHON_SOURCE))

from resolveops.synthetic_data import DEFAULT_SEED, generate_dataset  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=REPOSITORY_ROOT / "data/generated",
        help="directory beneath which synthetic/v1 will be written",
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    arguments = parser.parse_args()

    manifest = generate_dataset(arguments.output_root.resolve(), seed=arguments.seed)
    print(
        f"generated synthetic/{manifest.dataset_version} with seed {manifest.seed} "
        f"and {len(manifest.file_hashes)} hashed files"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
