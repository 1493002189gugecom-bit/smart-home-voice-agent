from __future__ import annotations

import pytest

from contracts import FaceCandidate, SpeakerCandidate
from registry import FaceRegistry, RegistryCorrupt, RegistryCryptoError, dpapi_protect, dpapi_unprotect
from speaker_registry import SpeakerRegistry

PACK = "speaker_test_v1"
DAD = "dad"
MOM = "mom"
GUEST = "person_" + "ab" * 16

# DPAPI exists only on Windows; the registry degrades to memory-only elsewhere,
# in which case the on-disk assertions below cannot mean anything.
from registry import _load_crypt32  # noqa: E402 - platform probe, used by the marker

requires_dpapi = pytest.mark.skipif(_load_crypt32() is None, reason="DPAPI is only available on Windows")


def vector(*values: float) -> tuple[float, ...]:
    return tuple(float(value) for value in values)


def test_round_trip_ranks_the_nearest_prototype(tmp_path):
    registry = SpeakerRegistry(tmp_path, PACK)
    registry.replace(DAD, [vector(1, 0, 0), vector(0.9, 0.1, 0)])
    registry.replace(MOM, [vector(0, 1, 0)])

    candidate = registry.rank(vector(1, 0, 0))[0]

    assert isinstance(candidate, SpeakerCandidate)
    assert candidate.person_id == DAD
    assert candidate.similarity == pytest.approx(1.0)
    assert candidate.margin is not None and candidate.margin > 0.5


def test_an_empty_gallery_ranks_nobody(tmp_path):
    candidate = SpeakerRegistry(tmp_path, PACK).rank(vector(1, 0, 0))[0]

    assert candidate.person_id is None
    assert candidate.similarity is None
    assert candidate.margin is None


def test_an_unknown_embedding_still_names_the_closest_person(tmp_path):
    """Thresholds are policy and stay with the caller, exactly as for faces."""
    registry = SpeakerRegistry(tmp_path, PACK)
    registry.replace(DAD, [vector(1, 0, 0)])

    candidate = registry.rank(vector(-1, 0, 0))[0]

    assert candidate.person_id == DAD
    assert candidate.similarity == pytest.approx(-1.0)


def test_an_invalid_person_id_is_refused(tmp_path):
    registry = SpeakerRegistry(tmp_path, PACK)

    with pytest.raises(ValueError):
        registry.replace("guests", [vector(1, 0, 0)])


def test_mismatched_dimensions_are_refused(tmp_path):
    registry = SpeakerRegistry(tmp_path, PACK)
    registry.replace(DAD, [vector(1, 0, 0)])

    with pytest.raises(ValueError):
        registry.replace(MOM, [vector(1, 0, 0, 0)])


def test_delete_reports_whether_anything_was_stored(tmp_path):
    registry = SpeakerRegistry(tmp_path, PACK)
    registry.replace(DAD, [vector(1, 0, 0)])

    assert registry.persons() == [DAD]
    assert registry.counts() == {DAD: 1}
    assert registry.delete(DAD) is True
    assert registry.delete(DAD) is False
    assert registry.persons() == []


def test_dynamically_added_people_are_accepted(tmp_path):
    """A person added at runtime must be enrollable exactly like the built-ins."""
    registry = SpeakerRegistry(tmp_path, PACK)
    registry.replace(GUEST, [vector(1, 0, 0)])

    assert registry.persons() == [GUEST]


@requires_dpapi
def test_a_face_file_copied_over_the_voiceprint_file_is_refused(tmp_path):
    """The two galleries must never be interchangeable, even for one user."""
    FaceRegistry(tmp_path, PACK).replace(DAD, [vector(1, 0, 0)])
    (tmp_path / "speaker_registry.bin").write_bytes((tmp_path / "face_registry.bin").read_bytes())

    with pytest.raises(RegistryCorrupt):
        SpeakerRegistry(tmp_path, PACK).load()


@requires_dpapi
def test_entropy_labels_keep_the_two_galleries_separate():
    ciphertext = dpapi_protect(b"secret", b"smart-home/vision/speaker-registry/v1")

    assert dpapi_unprotect(ciphertext, b"smart-home/vision/speaker-registry/v1") == b"secret"
    with pytest.raises(RegistryCryptoError):
        dpapi_unprotect(ciphertext, b"smart-home/vision/face-registry/v1")


@requires_dpapi
def test_a_truncated_file_is_reported_as_corrupt(tmp_path):
    SpeakerRegistry(tmp_path, PACK).replace(DAD, [vector(1, 0, 0)])
    path = tmp_path / "speaker_registry.bin"
    path.write_bytes(path.read_bytes()[:8])

    with pytest.raises(RegistryCorrupt):
        SpeakerRegistry(tmp_path, PACK).load()


@requires_dpapi
def test_a_file_written_for_another_model_pack_is_refused(tmp_path):
    SpeakerRegistry(tmp_path, PACK).replace(DAD, [vector(1, 0, 0)])

    with pytest.raises(RegistryCorrupt):
        SpeakerRegistry(tmp_path, "another_pack").load()


@requires_dpapi
def test_replacing_leaves_no_temporary_file_behind(tmp_path):
    registry = SpeakerRegistry(tmp_path, PACK)
    registry.replace(DAD, [vector(1, 0, 0)])
    registry.replace(DAD, [vector(0, 1, 0)])

    assert [item.name for item in tmp_path.iterdir() if item.name.startswith(".")] == []


@requires_dpapi
def test_the_face_registry_keeps_its_own_file_and_magic(tmp_path):
    """Regression for the shared-base refactor: faces must not move or change."""
    face = FaceRegistry(tmp_path, PACK)

    assert face.path.name == "face_registry.bin"
    assert face.magic == b"DSHFACE1"
    assert face.temp_prefix == ".face_registry."


def test_the_face_registry_also_reports_an_anti_parallel_match(tmp_path):
    """Regression: -1.0 doubled as the "no score yet" sentinel, so a perfectly
    anti-correlated embedding was reported as "nobody is enrolled" — in both
    galleries, because they now share this ranking code."""
    registry = FaceRegistry(tmp_path, PACK)
    registry.replace(DAD, [vector(1, 0, 0)])

    candidate = registry.rank(vector(-1, 0, 0))[0]

    assert isinstance(candidate, FaceCandidate)
    assert candidate.person_id == DAD
    assert candidate.similarity == pytest.approx(-1.0)
