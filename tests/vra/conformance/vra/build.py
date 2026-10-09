"""
Build tests/vra/conformance/vra/sample.vra-ng from the tree next to it (sample/), deterministically.

    python tests/vra/conformance/vra/build.py

The tree is the reviewable source; the .vra-ng is what a customer sends. test_vra.py checks that the
committed .vra-ng matches the tree, so edit the tree and re-run this.
"""
from __future__ import annotations

import io
import os
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
TREE = os.path.join(HERE, "sample")
OUT = os.path.join(HERE, "sample.vra-ng")


def build_bytes(tree: str = TREE) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for dp, dirs, files in os.walk(tree):
            dirs.sort()
            for fn in sorted(files):
                p = os.path.join(dp, fn)
                info = zipfile.ZipInfo(os.path.relpath(p, tree).replace(os.sep, "/"), date_time=(2026, 9, 30, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                with open(p, "rb") as f:
                    z.writestr(info, f.read())
    return buf.getvalue()


if __name__ == "__main__":
    with open(OUT, "wb") as f:
        f.write(build_bytes())
    print(f"wrote {os.path.relpath(OUT)}")
