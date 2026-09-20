"""The Inverter Charge Night integration."""

import logging
from datetime import timedelta

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv, issue_registry as ir
from homeassistant.helpers.typing import ConfigType

from .const import (
    AUTO_EFFICIENCY_KEYS,
    CONF_AUTO_EFFICIENCY_DATA,
    CONF_AUTO_EFFICIENT_CHARGE,
    CONF_BATTERY_SOC_ENTITY,
    CONF_END_TIME,
    CONF_HOUSE_LOAD_ENTITY,
    CONF_GRID_CHARGE_SWITCH,
    CONF_MIN_SOC_ENTITY,
    CONF_OPERATION_MODE,
    CONF_START_TIME,
    CONF_UPDATE_INTERVAL,
    DEFAULT_END_TIME,
    DEFAULT_OPERATION_MODE,
    DEFAULT_START_TIME,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    LEGACY_HOUSE_LOAD_ENERGY_ENTITY,
    LEGACY_INVERTER_KEYS,
    LEGACY_UNUSED_DATA_KEYS,
    LEGACY_UNUSED_OPTION_KEYS,
)
from .coordinator import (
    InverterChargeNightConfigEntry,
    InverterChargeNightCoordinator,
)
from .services import async_setup_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.BINARY_SENSOR,
    Platform.NUMBER,
    Platform.SELECT,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

__all__ = [
    "CONFIG_SCHEMA",
    "PLATFORMS",
    "InverterChargeNightConfigEntry",
    "InverterChargeNightCoordinator",
    "async_setup",
    "async_setup_entry",
    "async_unload_entry",
    "async_update_entry",
]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the actions once, before any entry is set up.

    The quality scale asks for this: an action that only exists while an entry
    happens to be loaded disappears from an automation's reach exactly when
    something went wrong. The handlers check the entry themselves instead.
    """
    async_setup_services(hass)
    return True


def _clear_stale_entity_issues(hass: HomeAssistant) -> None:
    """Drop "entity not available" issues that nobody can act on any more.

    The issue is not fixable from the repairs page, so one raised for an entity
    that has since been renamed - or replaced when the inverter integration
    changed - would stay red for ever, with no way for the user to clear it.
    An issue is dropped once its entity exists again, or once no configuration
    entry of this integration names it any more.
    """
    registry = ir.async_get(hass)
    configured: set[str] = set()
    for other in hass.config_entries.async_entries(DOMAIN):
        configured.update(
            str(value)
            for value in {**other.data, **other.options}.values()
            if isinstance(value, str) and "." in value
        )
    prefix = "entity_not_available_"
    for (domain, issue_id) in list(registry.issues):
        if domain != DOMAIN or not issue_id.startswith(prefix):
            continue
        entity_id = issue_id[len(prefix):]
        if entity_id not in configured or hass.states.get(entity_id) is not None:
            _LOGGER.debug("Clearing the stale repair issue for %s", entity_id)
            ir.async_delete_issue(hass, DOMAIN, issue_id)


def _migrate_entry_data(hass: HomeAssistant, entry: InverterChargeNightConfigEntry) -> None:
    """Carry settings of earlier versions over, and drop what nobody reads.

    Two things accumulate in a config entry over the life of an integration:
    settings whose key has been renamed, and data written by a version that no
    longer exists. The second kind is not harmless - one installation carried
    500 dead measurement records, a hundred kilobytes that Home Assistant
    loads, writes back and stores with every single change.
    """
    data = dict(entry.data)
    options = dict(entry.options)
    changed = False

    for legacy_key, new_key in LEGACY_INVERTER_KEYS.items():
        legacy_entity = data.pop(legacy_key, None)
        if legacy_entity is None:
            continue
        changed = True
        if not data.get(new_key):
            # Same entity under a name that does not carry a vendor: the wizard
            # writes the new key from 3.0.2 on, and everything reads only that.
            data[new_key] = legacy_entity
            _LOGGER.info("Carried %s over to %s", legacy_key, new_key)

    legacy_load_meter = data.pop(LEGACY_HOUSE_LOAD_ENERGY_ENTITY, None)
    if legacy_load_meter and not data.get(CONF_HOUSE_LOAD_ENTITY):
        # Same quantity under a new name: a cumulative kWh meter of the house.
        data[CONF_HOUSE_LOAD_ENTITY] = legacy_load_meter
        _LOGGER.info(
            "Carried the house consumption meter %s over from an earlier version",
            legacy_load_meter,
        )
    changed = changed or legacy_load_meter is not None

    for key in LEGACY_UNUSED_DATA_KEYS:
        if key in data:
            value = data.pop(key)
            changed = True
            if value:
                _LOGGER.info(
                    "The setting %r from an earlier version is no longer used. Its entity "
                    "%s is a good choice for the efficiency search's energy meters in the "
                    "options",
                    key,
                    value,
                )

    for key in LEGACY_UNUSED_OPTION_KEYS:
        if key in options:
            options.pop(key)
            changed = True

    efficiency = options.get(CONF_AUTO_EFFICIENCY_DATA)
    if isinstance(efficiency, dict):
        kept = {k: v for k, v in efficiency.items() if k in AUTO_EFFICIENCY_KEYS}
        if kept != efficiency:
            dropped = sorted(set(efficiency) - set(kept))
            _LOGGER.info(
                "Dropping efficiency data of an earlier version from the configuration: %s",
                ", ".join(dropped),
            )
            options[CONF_AUTO_EFFICIENCY_DATA] = kept
            changed = True

    if changed:
        hass.config_entries.async_update_entry(entry, data=data, options=options)


async def async_setup_entry(hass: HomeAssistant, entry: InverterChargeNightConfigEntry) -> bool:
    """Set up Inverter Charge Night from a config entry."""
    _migrate_entry_data(hass, entry)
    # Test-before-setup: verify critical entities are available
    required_entities = [
        entry.data.get(key)
        for key in (CONF_BATTERY_SOC_ENTITY, CONF_MIN_SOC_ENTITY, CONF_GRID_CHARGE_SWITCH)
    ]
    for entity_id in required_entities:
        if entity_id and hass.states.get(entity_id) is None:
            ir.async_create_issue(
                hass,
                DOMAIN,
                f"entity_not_available_{entity_id}",
                is_fixable=False,
                issue_domain=DOMAIN,
                severity=ir.IssueSeverity.ERROR,
                translation_key="entity_not_available",
                translation_placeholders={"entity_id": entity_id},
            )
            # Translated, so the message in the UI matches the repair issue above
            raise ConfigEntryNotReady(
                translation_domain=DOMAIN,
                translation_key="entity_not_available",
                translation_placeholders={"entity_id": entity_id},
            )
    # All required entities are known: clear issues from earlier failed attempts,
    # including ones left behind by entities that are no longer configured.
    _clear_stale_entity_issues(hass)

    coordinator = InverterChargeNightCoordinator(hass, entry)
    coordinator.review_discharge_block_risk()
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator

    # Set up platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Set up time-based triggers
    coordinator.setup_time_triggers()
    # Set up optional backup mode listener
    coordinator._setup_backup_mode_listener()
    # A reset the previous run could not finish is retried from here, not from
    # the constructor: only now is the coordinator reachable and the setup done.
    coordinator.async_start_pending_reset_retry()

    # Add update listener to handle config changes dynamically
    entry.async_on_unload(
        entry.add_update_listener(async_update_entry)
    )

    return True


async def async_update_entry(hass: HomeAssistant, entry: InverterChargeNightConfigEntry) -> None:
    """Handle config entry update."""
    # Safety check: ensure coordinator exists
    coordinator: InverterChargeNightCoordinator | None = getattr(entry, "runtime_data", None)
    if coordinator is None:
        _LOGGER.error("Coordinator not found for entry %s", entry.entry_id)
        return

    if coordinator.config == entry.data:
        # Only entry.options changed: the coordinator persists its runtime state
        # there (see _persist_state), which must not re-register triggers or
        # listeners. Configuration lives in entry.data.
        _LOGGER.debug("Entry options updated without configuration change; nothing to reconfigure")
        return

    _LOGGER.info("Configuration updated, updating triggers and coordinator")

    # Store old window times before update for comparison
    old_start_time = coordinator.config.get(CONF_START_TIME, DEFAULT_START_TIME)
    old_end_time = coordinator.config.get(CONF_END_TIME, DEFAULT_END_TIME)
    # Captured before the swap below: afterwards it is the new value, and the
    # listener that watches the old entity would never be re-armed.
    old_battery_soc_entity = coordinator.config.get(CONF_BATTERY_SOC_ENTITY)

    # The mode owns the window: a window still running in the old mode has to be
    # torn down before the new mode takes over, otherwise the options dialog
    # would leave force discharge on while grid charge is switched back on. The
    # select entity goes through the same method. Done before the config is
    # swapped so the reset still addresses the entities it wrote to.
    await coordinator.async_apply_operation_mode(
        entry.data.get(CONF_OPERATION_MODE, DEFAULT_OPERATION_MODE)
    )

    # Update coordinator config reference
    coordinator.config = entry.data
    coordinator.review_discharge_block_risk()
    coordinator.auto_efficient_charge = entry.data.get(CONF_AUTO_EFFICIENT_CHARGE, False)
    coordinator._auto_missing_entities_logged = False
    coordinator._house_load_cache = None  # the meter or the average may have changed

    # Update coordinator polling interval
    coordinator.update_interval = timedelta(
        seconds=entry.data.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)
    )

    # Update time triggers with new configuration
    try:
        coordinator.update_time_triggers(previous_soc_entity=old_battery_soc_entity)
        coordinator._setup_backup_mode_listener()
    except Exception as e:
        _LOGGER.error("Error updating time triggers: %s", e, exc_info=True)
        # Restore old config if update failed
        coordinator.config = entry.data  # Still use new data, but log error
        return

    # Check if window changed and we need to adjust state
    new_start_time = entry.data.get(CONF_START_TIME, DEFAULT_START_TIME)
    new_end_time = entry.data.get(CONF_END_TIME, DEFAULT_END_TIME)

    # If window times changed, check if we need to reset or start
    if old_start_time != new_start_time or old_end_time != new_end_time:
        _LOGGER.info(
            "Window changed from %s-%s to %s-%s, checking current state",
            old_start_time,
            old_end_time,
            new_start_time,
            new_end_time,
        )
        # _check_current_window will handle state adjustment

    # Request refresh to recalculate with new settings
    await coordinator.async_request_refresh()


async def async_unload_entry(hass: HomeAssistant, entry: InverterChargeNightConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator = entry.runtime_data
        # From here on no timer may be armed any more: an async_call_later
        # scheduled during the unload would fire on a dead coordinator. A failed
        # reset is still persisted, so the next setup retries it.
        coordinator._unloading = True
        # CRITICAL: Reset settings before unloading to prevent leaving inverter in bad state
        if coordinator.is_active:
            _LOGGER.warning("Integration unloading during active window - resetting settings")
            try:
                await coordinator._reset_settings()
            except Exception as e:
                _LOGGER.error("Error resetting settings during unload: %s", e, exc_info=True)
                # Continue with unload even if reset fails - we tried our best
        coordinator.remove_time_triggers()
        coordinator._cancel_window_check()
        coordinator._remove_battery_soc_listener()
        coordinator._remove_inverter_min_soc_listener()
        coordinator._remove_grid_import_listener()
        coordinator._remove_auto_sample_listener()
        coordinator._remove_backup_mode_listener()
        await coordinator._stop_periodic_verification()
        coordinator._cancel_skip_next_expiry()
        # A pending reset stays persisted and is retried after the next setup
        coordinator._cancel_reset_retry()

    return unload_ok
