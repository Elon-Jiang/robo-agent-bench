"""Step 2 check: build the scene and render each camera to PNG."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image  # noqa: E402

from robobench.sim import Sim  # noqa: E402

out = Path(__file__).resolve().parent.parent / "outputs"
out.mkdir(exist_ok=True)

sim = Sim(width=640, height=480)
sim.step(200)  # let the cube settle
print("tcp at home:", sim.site_pos("tcp").round(3))
print("cube:", sim.body_pos("cube").round(3))
for cam in ("front", "top", "side"):
    Image.fromarray(sim.render(cam)).save(out / f"render_{cam}.png")
    print("saved", out / f"render_{cam}.png")
