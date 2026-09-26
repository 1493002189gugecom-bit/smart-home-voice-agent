"""The checked-in vision config and the dataclass must not drift apart.

`VisionConfig.load` rejects any key it does not know and any field it cannot find,
so adding a field to the dataclass without the JSON (or the other way round) fails
at service startup. This asserts the same invariant without touching model paths.
"""

from __future__ import annotations

import json
from pathlib import Path

import config as vision_config

CONFIG_PATH = Path(__file__).parents[1] / "config" / "vision.json"


def test_the_config_file_keys_are_exactly_the_declared_fields():
    raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

    assert set(raw) == vision_config._FIELDS


def test_the_dataclass_fields_are_exactly_the_declared_keys():
    fields = set(vision_config.VisionConfig.__dataclass_fields__)

    assert fields == vision_config._FIELDS


def test_the_speaker_gallery_has_its_own_model_pack():
    """A voiceprint written by one model must never be ranked by another."""
    raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

    assert raw["speaker_pack"] != raw["insightface_pack"]


def test_the_real_config_loads_and_carries_the_speaker_pack(tmp_path):
    """Key-set equality alone would not catch a typo in the loader itself."""
    raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    raw["pose_model"] = str(tmp_path / "pose.pt")
    raw["insightface_root"] = str(tmp_path / "insightface")
    raw["runtime_dir"] = str(tmp_path / "runtime")
    path = tmp_path / "vision.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    loaded = vision_config.VisionConfig.load(path)

    assert loaded.speaker_pack == raw["speaker_pack"]
    assert loaded.insightface_pack == raw["insightface_pack"]
