"""Previous evidence races at the review entry; native outputs are synthetic."""

import json

import pytest
from test_advanced_review_copy import audit, entry, put, visual_and_freeze
from test_advanced_review_copy import session as session


def predecessor_scene(s):
    assert entry.main() == 0
    visual_and_freeze(s)
    baseline = s.work
    s.argv[s.argv.index("--out") + 1] = str(baseline.parent / "stage-S1")
    s.argv[s.argv.index("--stage") + 1] = "S1"
    s.argv += ["--baseline", str(baseline), "--previous", str(baseline)]
    s.work = baseline.parent / "stage-S1"
    assert entry.main() == 0
    visual_and_freeze(s)
    previous = s.work
    s.work = baseline.parent / "stage-S2"
    s.argv[s.argv.index("--out") + 1] = str(s.work)
    s.argv[s.argv.index("--stage") + 1] = "S2"
    s.argv[s.argv.index("--previous") + 1] = str(previous)
    return {"baseline": baseline, "previous": previous}


def mutate(prior, target):
    path = (
        prior
        / "snapshot"
        / {
            "observation": "review/native-observation-xml-identity.json",
            "manifest": "MANIFEST.json",
            "stage-metadata": "review/START.json",
        }[target]
    )
    data = json.loads(path.read_bytes())
    data["injected_drift"] = True
    if target == "stage-metadata":
        data["stage"] = "advanced-S4"
    put(path, data)


@pytest.mark.parametrize("source", ["baseline", "previous"])
@pytest.mark.parametrize("target", ["observation", "manifest", "stage-metadata"])
@pytest.mark.parametrize("phase", ["initialize-xml", "before-geometry", "before-freeze"])
def test_predecessor_drift_cannot_inherit_pass(session, source, target, phase):
    priors = predecessor_scene(session)
    if phase == "before-freeze":
        assert entry.main() == 0
        mutate(priors[source], target)
        with pytest.raises(ValueError, match="(?i)(drift|changed|differ)"):
            visual_and_freeze(session)
    else:
        session.inject = lambda name: mutate(priors[source], target) if name == phase else None
        with pytest.raises(ValueError, match="(?i)(drift|changed|differ)"):
            entry.main()
        assert not (session.work / "review/READY.json").exists()
    with pytest.raises(ValueError):
        audit.verify(
            session.work,
            session.package / "rc/execution",
            session.work / "packet",
            session.work / "review",
        )
    assert not (session.work / "snapshot/MANIFEST.json").exists()


def test_stage_and_source_paths_come_only_from_verified_snapshot(session):
    priors = predecessor_scene(session)
    for prior in priors.values():
        path = prior / "review/START.json"
        data = json.loads(path.read_bytes())
        data["stage"] = "unbound-sidecar-wrong-stage"
        data["inputs"]["external_files"]["source:rc4.kicad_pcb"]["path"] = "unbound-wrong-path"
        put(path, data)
    assert entry.main() == 0
    visual_and_freeze(session)
    audit.verify_snapshot(session.work / "snapshot")


def test_verified_bytes_must_match_the_new_binding(session, monkeypatch):
    priors = predecessor_scene(session)
    original = audit.verify_snapshot

    def changed_after_verification(snapshot):
        result = original(snapshot)
        if snapshot == priors["baseline"] / "snapshot":
            mutate(priors["baseline"], "observation")
        return result

    monkeypatch.setattr(audit, "verify_snapshot", changed_after_verification)
    with pytest.raises(ValueError, match="(?i)(drift|changed|differ)"):
        entry.main()
    assert not (session.work / "review/READY.json").exists()
