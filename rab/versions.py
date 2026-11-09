"""Protocol and scene revisions; safe to import without MuJoCo."""

SUITE_VERSION = "0.2.1"


def scene_version(task):
    # tidy_desk v4 (suite 0.2.1): primitive stationery colliders and contact
    # compliance that also applies against the higher-priority finger pads.
    # Neither v2 nor v3 tidy records describe this physical scene.
    return 4 if task == "pilot_tidy_desk" else 1


def recorded_scene_version(record):
    """Old v0.1 records predate the scene_version field; do not guess older runs."""
    if record.get("scene_version") is not None:
        return record["scene_version"]
    if record.get("suite_version") == "0.1":
        return 1
    return "unknown"
