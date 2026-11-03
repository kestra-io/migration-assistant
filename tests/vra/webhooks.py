"""
The fake Slack and Teams webhook URLs the tests and fixtures plant, put together here at run time so that no file in
the repository holds one whole: a whole webhook URL reads as a leaked secret to a secret scanner, fake or not.

A fixture holds a marker, `@WEBHOOK:<name>@`, where a URL goes. `copy` copies a tree and writes the URLs in place
of the markers; `filled` does it once per run into a temporary folder and returns that folder.
"""
from __future__ import annotations

import atexit
import os
import re
import shutil
import tempfile

SLACK = "https://" + "hooks.slack.com" + "/services/"
TEAMS = "webhook.office.com"

URLS = {
    # shaped so they cannot pass for real ones
    "slack-planted": SLACK + "PLANTED/FAKE/not-a-real-hook",
    "teams-planted": "https://acme." + TEAMS + "/webhookb2/planted-not-a-real-hook",
    "slack-detect": SLACK + "DETECT/FAKE/not-a-real-hook",
    "teams-detect": "https://example." + TEAMS + "/webhookb2/detect-not-a-real-hook",
    # shaped like a real one: all-zero team and bot IDs, the alphabet as the token
    "slack-shaped": SLACK + "T00000000/B00000000/abcdefghijklmnopqrstuvwx",
}
MARKER = re.compile(r"@WEBHOOK:([a-z-]+)@")


def marker(name: str) -> str:
    return f"@WEBHOOK:{name}@"


def fill(tree: str) -> None:
    """Write the URL in place of every marker in the text files under `tree`."""
    for dp, _d, fs in os.walk(tree):
        for f in fs:
            p = os.path.join(dp, f)
            try:
                text = open(p, encoding="utf-8").read()
            except UnicodeDecodeError:
                continue
            if "@WEBHOOK:" in text:
                with open(p, "w", encoding="utf-8") as fh:
                    fh.write(MARKER.sub(lambda m: URLS[m.group(1)], text))


def copy(src: str, dst) -> str:
    shutil.copytree(src, dst)
    fill(str(dst))
    return str(dst)


_FILLED: dict[str, str] = {}


def filled(src: str) -> str:
    """A copy of `src` with its URLs written in, made once per run, under the same folder name."""
    if src not in _FILLED:
        tmp = tempfile.mkdtemp(prefix="webhooks-")
        atexit.register(shutil.rmtree, tmp, True)
        _FILLED[src] = copy(src, os.path.join(tmp, os.path.basename(src)))
    return _FILLED[src]
