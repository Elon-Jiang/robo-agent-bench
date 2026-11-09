"""Local lifecycle hooks; persist before rendering/cleanup can block or fail."""

import io
import signal
from rab import state
from rab.metrics import parse_self_report

BACKEND = None
_INSTALLED = False


def finalize(reason=None):
    dev = state.STATE.device
    if (
        state.STATE.pending is not None
        and not state.STATE.finalized
        and hasattr(dev, "scene")
        and hasattr(dev, "sim")
    ):
        # Signals can arrive between a physics step and its scoring callback.
        state.STATE.pending = dev.evaluate(dev.sim.mj_data)
    backend = BACKEND
    if backend is None:
        state.flush_episode(reason or "process_exit")
        return
    if reason is None:
        reason = getattr(backend, "_rab_end_reason", None)
        if reason is None:
            reason = (
                "agent_done"
                if backend._done
                else (
                    "turn_cap"
                    if 0 < backend.max_turns <= backend.turn
                    else "episode_limit"
                )
            )
    state.flush_episode(
        reason,
        turns_used=backend.turn,
        self_report=getattr(backend, "_rab_self_report", None),
    )


def install():
    global _INSTALLED
    if _INSTALLED:
        return
    from mujoco_playground.tasks.agentic.tabletop_v0 import agent as A

    original_reset = A.BridgeBackend.on_reset
    original_handler = A.BridgeBackend._handler
    original_finish = A.AgentPolicy.finish
    original_close = A.BridgeBackend.close
    original_consume_reset = A.BridgeBackend.consume_reset_request

    def reset(self, obs):
        global BACKEND
        BACKEND = self
        self._rab_end_reason = self._rab_self_report = None
        return original_reset(self, obs)

    def handler(self):
        base = original_handler(self)
        backend = self

        class Handler(base):
            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                body = raw.decode()
                if self.path.startswith("/done"):
                    _, thought = A._extract_plan_metadata(body)
                    backend._rab_self_report = parse_self_report(body)
                    if backend._rab_self_report is None and thought:
                        backend._rab_self_report = parse_self_report(thought)
                    backend._rab_end_reason = (
                        "harness_done" if "[harness" in body else "agent_done"
                    )
                    if "[harness:time_cap]" in body:
                        backend._rab_end_reason = "time_cap"
                    if "[harness:interrupt]" in body:
                        backend._rab_end_reason = "interrupt"
                saved = self.rfile
                self.rfile = io.BytesIO(raw)
                try:
                    return super().do_POST()
                finally:
                    self.rfile = saved

        return Handler

    def finish(self, obs, success):
        finalize()
        return original_finish(self, obs, success)

    def close(self):
        finalize()
        return original_close(self)

    def consume_reset(self):
        requested = original_consume_reset(self)
        if requested:
            self._rab_end_reason = "reset"
        return requested

    A.BridgeBackend.on_reset = reset
    A.BridgeBackend._handler = handler
    A.BridgeBackend.close = close
    A.BridgeBackend.consume_reset_request = consume_reset
    A.AgentPolicy.finish = finish
    _INSTALLED = True


def install_signals():
    def interrupted(signum, frame):
        finalize("interrupt" if signum == signal.SIGINT else "terminated")
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, interrupted)
    signal.signal(signal.SIGTERM, interrupted)
