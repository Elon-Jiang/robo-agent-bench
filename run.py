"""Entry point: register robo-agent-bench tasks, then run tabletop_v0.

../mujoco_playground/.venv/bin/python run.py --agent=oracle --task=spill_wipe --episodes=3
"""

import rab

rab.register()

from absl import app  # noqa: E402
from mujoco_playground.tasks.agentic.tabletop_v0 import run  # noqa: E402

if __name__ == "__main__":
    from rab.episode_lifecycle import install_signals

    install_signals()
    app.run(run.main)
