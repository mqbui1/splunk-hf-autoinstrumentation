import json
import time
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class InjectionState:
    """
    Persists which PIDs have been successfully injected so the daemon loop
    never double-injects a process.  State is keyed by PID; entries for dead
    processes are pruned on each cycle.
    """

    def __init__(self, state_file: str):
        self._path = Path(state_file)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._data: dict = self._load()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def is_injected(self, pid: int) -> bool:
        return str(pid) in self._data

    def mark_injected(self, pid: int, service_name: str):
        self._data[str(pid)] = {
            "service_name": service_name,
            "injected_at": time.time(),
        }
        self._save()
        logger.debug(f"State: marked PID {pid} ({service_name}) as injected")

    def clean_dead_pids(self, live_pids: set[int]):
        """Remove entries for processes that are no longer running."""
        dead = [p for p in self._data if int(p) not in live_pids]
        if dead:
            for p in dead:
                del self._data[p]
            self._save()
            logger.debug(f"State: pruned {len(dead)} dead PID(s)")

    def all_entries(self) -> dict:
        return dict(self._data)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load(self) -> dict:
        if self._path.exists():
            try:
                return json.loads(self._path.read_text())
            except Exception as exc:
                logger.warning(f"Could not read state file {self._path}: {exc} — starting fresh")
        return {}

    def _save(self):
        try:
            self._path.write_text(json.dumps(self._data, indent=2))
        except Exception as exc:
            logger.error(f"Could not write state file {self._path}: {exc}")
