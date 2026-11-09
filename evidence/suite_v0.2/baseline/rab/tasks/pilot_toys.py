"""pilot_toys household pilot (YAM)."""

from functools import partial
from rab.tasks.pilots import SCENES, task_for, extend_config as _extend

NAME = "pilot_toys"
PROMPT = SCENES[NAME].prompt
TASK = task_for(NAME)
extend_config = partial(_extend, name=NAME)
