"""Package src/fbc as build/lambda.zip for the two Lambda functions (see infra/terraform).

The deployed handlers (fbc.lambdas) only need boto3/botocore, which the python3.12 Lambda
runtime already provides, so the zip contains just this package.
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    out = ROOT / "build" / "lambda.zip"
    out.parent.mkdir(exist_ok=True)
    src = ROOT / "src"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted((src / "fbc").rglob("*.py")):
            info = zipfile.ZipInfo(
                str(path.relative_to(src)).replace("\\", "/"), (2026, 1, 1, 0, 0, 0)
            )
            info.external_attr = 0o644 << 16
            zf.writestr(info, path.read_bytes())
    sys.stdout.write(f"wrote {out} ({out.stat().st_size} bytes)\n")


if __name__ == "__main__":
    main()
