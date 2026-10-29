"""
Shared fixtures.

`conformance_stages(estate)` builds the stage folder of one synthetic conformance estate (tests/vra/conformance/
<estate>/: review, helpers, events, builders, triage) the way a customer gets it, with no model, in a temporary
folder, once per session: its vRO tree and its vRA export (if any) are copied into an export folder, its specs/
seed data/2-understand/ as if Understand had written them, and preflight, assess and brief run with
VRO_NO_MODEL=1. The webhook URLs a tree marks are written into its copy (webhooks.py). Stage output is not committed, so tests that read an estate's assess.json or brief.json build
it here.
"""
from __future__ import annotations

import os
import shutil
import sys

import pytest
import webhooks as W

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

CONFORMANCE = os.path.join(ROOT, "tests", "vra", "conformance")


@pytest.fixture(scope="session")
def conformance_stages(tmp_path_factory):
    built: dict[str, str] = {}

    def get(estate: str) -> str:
        if estate in built:
            return built[estate]
        from kestra_migration_assistant.vra.stages import assess, brief, preflight
        from kestra_migration_assistant.vra.stages import common as C
        here = os.path.join(CONFORMANCE, estate)
        tmp = tmp_path_factory.mktemp(estate)
        src, stages = tmp / "sources" / estate, tmp / "stages" / estate
        # the tree under the estate's name, so workflow IDs (<estate>-<name>) match the committed specs
        W.copy(os.path.join(here, "vro"), src / estate)
        if os.path.isdir(os.path.join(here, "vra")):
            W.copy(os.path.join(here, "vra"), src / f"{estate}-vra")
        understand = stages / C.LAYOUT["understand"]
        understand.mkdir(parents=True)
        for f in sorted(os.listdir(os.path.join(here, "specs"))):
            if f.endswith(".json"):
                shutil.copy(os.path.join(here, "specs", f), understand / f)
        old = os.environ.get("VRO_NO_MODEL")
        os.environ["VRO_NO_MODEL"] = "1"
        try:
            C.configure(estate, source_dir=str(src), stages_dir=str(stages))
            preflight.run()
            assess.run()
            brief.run()
        finally:
            if old is None:
                os.environ.pop("VRO_NO_MODEL", None)
        built[estate] = str(stages)
        return built[estate]
    return get


def answer(q: str, text, **kw) -> None:
    """Answer one item in the configured estate's your-input/answers.yaml as a person would: a preflight
    question by its id (Q9) or any item by its key."""
    import re as _re

    from kestra_migration_assistant.vra.stages import preflight, your_input
    key = preflight.answer_key(q) if _re.fullmatch(r"Q\d+", q) else q
    your_input.set_answer(key, text, **kw)
