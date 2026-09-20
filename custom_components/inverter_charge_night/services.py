"""Service actions.

The integration is a planner: it works out how full the battery has to be and
writes that to the inverter on its own schedule. What it could not do until now
is answer a question, or take an instruction, from a Home Assistant automation -
it registered no actions at all, so the only way in was to toggle its entities.

These actions close that gap. They are deliberately thin: every one of them
routes through a coordinator method that already carries the safety interlocks
(backup mode, the house connection limit, the capture-then-restore contracts),
so an automation can never reach the inverter by a path the integration itself
does not guard.

Registered from ``async_setup`` rather than ``async_setup_entry`` so that they
exist even while no entry is loaded; each handler resolves and checks its entry
and raises a translated error instead of failing quietly.
"""

from __future__ import annotations

import logging
from datetime import timedelta

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .const import (
    ATTR_CONFIG_ENTRY_ID,
    ATTR_DURATION,
    ATTR_TARGET_SOC,
    DEFAULT_ADHOC_DURATION_MIN,
    DOMAIN,
    SERVICE_ALLOW_DISCHARGE,
    SERVICE_BLOCK_DISCHARGE,
    SERVICE_CHARGE_TO,
    SERVICE_PLAN_TARGET_SOC,
    SERVICE_RESET_INVERTER,
)
from .coordinator import InverterChargeNightConfigEntry, InverterChargeNightCoordinator

_LOGGER = logging.getLogger(__name__)

_ENTRY_SCHEMA = vol.Schema({vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string})
_DEFAULT_DURATION = timedelta(minutes=DEFAULT_ADHOC_DURATION_MIN)
_CHARGE_TO_SCHEMA = _ENTRY_SCHEMA.extend(
    {
        vol.Required(ATTR_TARGET_SOC): vol.All(
            vol.Coerce(float), vol.Range(min=0, max=100)
        ),
        vol.Optional(ATTR_DURATION, default=_DEFAULT_DURATION): cv.positive_time_period,
    }
)
_DURATION_SCHEMA = _ENTRY_SCHEMA.extend(
    {vol.Optional(ATTR_DURATION, default=_DEFAULT_DURATION): cv.positive_time_period}
)


def _coordinator_for(hass: HomeAssistant, call: ServiceCall) -> InverterChargeNightCoordinator:
    """Resolve the entry this call is about, or say precisely what is wrong.

    With exactly one configured inverter the field may be left out; with several
    it is required, because guessing which battery an automation meant is not a
    guess worth making.
    """
    entry_id: str | None = call.data.get(ATTR_CONFIG_ENTRY_ID)
    entries: list[InverterChargeNightConfigEntry] = hass.config_entries.async_entries(DOMAIN)
    loaded = [entry for entry in entries if entry.state is ConfigEntryState.LOADED]

    if entry_id is None:
        if len(loaded) == 1:
            return loaded[0].runtime_data
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="entry_id_required",
            translation_placeholders={"count": str(len(loaded))},
        )

    entry: InverterChargeNightConfigEntry | None = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="entry_not_found",
            translation_placeholders={"entry_id": entry_id},
        )
    if entry.state is not ConfigEntryState.LOADED:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="entry_not_loaded",
            translation_placeholders={"title": entry.title},
        )
    return entry.runtime_data


async def _async_plan_target_soc(call: ServiceCall) -> ServiceResponse:
    """Answer what the planner would do right now, and change nothing.

    Useful on its own - an automation can decide whether tonight is worth
    charging at all - and useful for looking at why a target came out as it did.
    """
    coordinator = _coordinator_for(call.hass, call)
    planned = await coordinator.preview_plan()
    if planned is None:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="plan_failed",
        )
    plan, plan_input = planned
    return {
        "target_soc": plan.target_soc,
        "reason": plan.reason,
        "lower_bound_soc": plan.lower_bound_soc,
        "upper_bound_soc": plan.upper_bound_soc,
        "bridge_kwh": round(plan.bridge_kwh, 3),
        "surplus_kwh": round(plan.surplus_kwh, 3),
        "current_soc": plan_input.current_soc,
        "forecast_kwh": round(plan_input.forecast_kwh_next_day, 3),
        "forecast_available": plan_input.forecast_available,
        "window_end": plan_input.window_end.isoformat(),
        "pv_crossover": plan_input.pv_crossover.isoformat(),
        "sunset": plan_input.sunset.isoformat(),
    }


async def _async_reset_inverter(call: ServiceCall) -> None:
    """Put the inverter back to the settings captured before the window.

    The same reset the window end runs, reachable from an automation - for the
    case where something outside this integration needs the inverter back now.
    A failure is reported rather than swallowed, and the integration's own retry
    ladder keeps trying either way.
    """
    coordinator = _coordinator_for(call.hass, call)
    if not await coordinator.async_reset_inverter():
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="reset_failed",
        )


async def _async_charge_to(call: ServiceCall) -> None:
    """Charge the battery to a level from the grid, for a while.

    Everything the night window does, on request: the house connection limit,
    the capture of the inverter's own settings, the restore when the window
    ends. Refused while the configured window is running - that one has the
    tariff behind it.
    """
    coordinator = _coordinator_for(call.hass, call)
    if not await coordinator.async_charge_to(
        float(call.data[ATTR_TARGET_SOC]), call.data[ATTR_DURATION]
    ):
        raise HomeAssistantError(translation_domain=DOMAIN, translation_key="adhoc_refused")


async def _async_block_discharge(call: ServiceCall) -> None:
    """Hold what is in the battery for a while, buying nothing."""
    coordinator = _coordinator_for(call.hass, call)
    if not await coordinator.async_block_discharge(call.data[ATTR_DURATION]):
        raise HomeAssistantError(translation_domain=DOMAIN, translation_key="adhoc_refused")


async def _async_allow_discharge(call: ServiceCall) -> None:
    """Release a hold or a charge before its time is up."""
    coordinator = _coordinator_for(call.hass, call)
    if not await coordinator.async_allow_discharge():
        raise HomeAssistantError(translation_domain=DOMAIN, translation_key="nothing_to_release")


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the integration's actions once per Home Assistant start."""
    hass.services.async_register(
        DOMAIN,
        SERVICE_PLAN_TARGET_SOC,
        _async_plan_target_soc,
        schema=_ENTRY_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_RESET_INVERTER,
        _async_reset_inverter,
        schema=_ENTRY_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_CHARGE_TO, _async_charge_to, schema=_CHARGE_TO_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_BLOCK_DISCHARGE, _async_block_discharge, schema=_DURATION_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_ALLOW_DISCHARGE, _async_allow_discharge, schema=_ENTRY_SCHEMA
    )
