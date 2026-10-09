"""
Shared fixtures.

`sample_stages(estate)` builds a sample estate's stage folder the way a customer gets it (`use-sample`, then
the stages with no model), in a temporary folder: stage output is no longer committed, so tests that read a
sample's assess.json or brief.json build it once per session. The sample's saved model output
(samples/<name>/model-output/: specs, cluster summaries, domain names, walkthrough text) seeds it, so the
result is the one `vro.py all --estate <name>-sample --no-model` writes.
"""
from __future__ import annotations

import os
import shutil
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

SAMPLE_OF = {"avi-sample": "avi", "nuage-sample": "nuage"}


@pytest.fixture(scope="session")
def sample_stages(tmp_path_factory):
    built: dict[str, str] = {}

    def get(estate: str) -> str:
        if estate in built:
            return built[estate]
        from kestra_migration_assistant.vra import cli
        from kestra_migration_assistant.vra.stages import assess, brief, preflight
        from kestra_migration_assistant.vra.stages import common as C
        name = SAMPLE_OF[estate]
        sample = os.path.join(ROOT, "samples", name)
        tmp = tmp_path_factory.mktemp(estate)
        src, stages = tmp / "sources" / estate, tmp / "stages" / estate
        shutil.copytree(sample, src, ignore=lambda d, names: [n for n in names if d == sample and n in cli.SAMPLE_EXTRAS])
        cli.seed_sample(sample, str(stages))
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
