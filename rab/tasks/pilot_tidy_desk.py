"""pilot_tidy_desk household pilot (YAM)."""

from functools import partial
from rab.tasks.pilots import SCENES, task_for, extend_config as _extend

NAME = "pilot_tidy_desk"
PROMPT = SCENES[NAME].prompt
TASK = task_for(NAME)
extend_config = partial(_extend, name=NAME)
