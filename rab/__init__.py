"""robo-agent-bench tasks on top of MuJoCo Playground tabletop_v0.

Call `register()` BEFORE importing `tabletop_v0.run`: run.py freezes its
--task enum from `task.TASKS` at import time.
"""

from __future__ import annotations

_REGISTERED = False
EXTENDERS: dict = {}


def register() -> None:
    global _REGISTERED
    if _REGISTERED:
        return
    from mujoco_playground.tasks.agentic.tabletop_v0 import task as T

    from rab.tasks import right_mug, spill_wipe
    from rab.tasks import (
        pilot_spill_cleanup,
        pilot_clear_dishes,
        pilot_tidy_desk,
        pilot_toys,
        pilot_breakfast,
    )

    right_mug.configure_local_assets()
    for mod in (
        spill_wipe,
        right_mug,
        pilot_spill_cleanup,
        pilot_clear_dishes,
        pilot_tidy_desk,
        pilot_toys,
        pilot_breakfast,
    ):
        T.TASKS[mod.NAME] = mod.TASK
        EXTENDERS[mod.NAME] = mod.extend_config

    from rab import bridge_privacy

    bridge_privacy.install(frozenset(EXTENDERS))
    from rab import episode_lifecycle

    episode_lifecycle.install()
    orig = T.env_config

    def env_config(task: str = "cube_in_bowl", robot: str = "so101", impl="cpp"):
        bridge_privacy.ACTIVE_TASK = task
        cfg = orig(task=task, robot=robot, impl=impl)
        if task in EXTENDERS:
            EXTENDERS[task](cfg, robot)
        return cfg

    T.env_config = env_config
    _REGISTERED = True
