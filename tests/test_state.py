import json
import time
from pathlib import Path

import pytest

from autoinstrumentation.state import InjectionState


@pytest.fixture
def state(tmp_path):
    return InjectionState(str(tmp_path / "state.json"))


class TestInjectionState:
    def test_not_injected_initially(self, state):
        assert not state.is_injected(1234)

    def test_mark_and_check(self, state):
        state.mark_injected(1234, "my-service")
        assert state.is_injected(1234)

    def test_persists_to_disk(self, tmp_path):
        path = str(tmp_path / "state.json")
        s1 = InjectionState(path)
        s1.mark_injected(9999, "svc")

        s2 = InjectionState(path)
        assert s2.is_injected(9999)

    def test_clean_dead_pids(self, state):
        state.mark_injected(100, "svc-a")
        state.mark_injected(200, "svc-b")
        state.clean_dead_pids({200})
        assert not state.is_injected(100)
        assert state.is_injected(200)

    def test_corrupted_state_file_resets(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text("not valid json")
        s = InjectionState(str(path))
        assert not s.is_injected(1)
