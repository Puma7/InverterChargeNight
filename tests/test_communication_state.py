"""Incident counting, retained recovery, restart, privacy and flood prevention."""
from datetime import UTC, datetime, timedelta

import pytest

from custom_components.inverter_charge_night.communication import CommunicationState, HISTORY_LIMIT

NOW = datetime(2026, 1, 15, 23, tzinfo=UTC)


def test_outage_confirmation_recovery_and_daily_reminder():
    state = CommunicationState()
    assert state.observe([], NOW) is False
    assert state.last_success_at == NOW.isoformat()
    assert state.observe(["battery_soc", "rest"], NOW, False) is True
    assert "nicht bestätigt" in state.message()
    assert state.observe(["rest", "battery_soc", "rest"], NOW, False) is False
    assert state.incidents == 1 and state.failed_samples == 2
    assert state.observe(["rest", "battery_soc"], NOW, True) is True
    assert "meldet Netzladen aus" in state.message()
    assert state.observe(["rest", "battery_soc"], NOW + timedelta(days=1), True) is True
    assert state.observe([], NOW + timedelta(days=1)) is True
    assert "wiederhergestellt" in state.message()
    assert state.observe([], NOW + timedelta(days=1)) is False
    assert state.observe(["modbus"], NOW + timedelta(days=2)) is True
    assert state.incidents == 2


def test_restart_reannounces_same_incident_without_counting_it_twice():
    original = CommunicationState()
    original.observe(["modbus"], NOW, False)
    saved = original.snapshot()
    saved["private_password"] = "do-not-export"
    saved["history"][0]["private_token"] = "do-not-export"
    restored = CommunicationState(saved)
    assert restored.observe(["modbus"], NOW, False) is True
    assert restored.incidents == 1
    assert "do-not-export" not in str(restored.snapshot())
    snapshot = restored.snapshot()
    snapshot["history"][0]["faults"].append("rest")
    assert restored.history[0]["faults"] == ["modbus"]


@pytest.mark.parametrize("saved", [
    None, [], "bad", {}, {"incidents": -1, "failed_samples": True},
    {"history": "not-a-list"}, {"history": [1]},
    {"history": [{"time": None, "faults": []}]},
    {"history": [{"time": NOW.isoformat(), "faults": "rest"}]},
    {"history": [{"time": NOW.isoformat(), "faults": ["private-secret"]}]},
    {"history": [{"time": "not-a-date", "faults": []}]},
])
def test_corrupt_persisted_metadata_is_ignored(saved):
    state = CommunicationState(saved)
    assert state.incidents == 0
    assert state.snapshot()["history"] == []


def test_bad_stop_feedback_is_unknown_and_history_is_bounded():
    state = CommunicationState({"history": [{"time": NOW.isoformat(), "faults": ["rest"],
                                             "stop_confirmed": "on"}]})
    assert state.stop_confirmed is None
    for day in range(40):
        state.observe([], NOW + timedelta(days=day))
        state.observe(["rest"], NOW + timedelta(days=day))
    assert len(state.snapshot()["history"]) == HISTORY_LIMIT
    assert state.incidents == 40
    restored = CommunicationState(state.snapshot())
    assert restored.failed_samples == state.failed_samples
    assert len(restored.history) == HISTORY_LIMIT


def test_unknown_program_fault_code_is_rejected():
    with pytest.raises(ValueError, match="Unknown communication"):
        CommunicationState().observe(["private-ip-or-error-message"], NOW)
