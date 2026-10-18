"""Wait for observed KORE entities in every lab setup, including Container."""
import asyncio

from homeassistant.helpers.entity_component import DATA_INSTANCES


async def wait_for_kore_feedback(hass, mapping, timeout=30):
    values = {}
    try:
        async with asyncio.timeout(timeout):
            while True:
                # Enabling registry entities may reload KORE. Always resolve the
                # current entities instead of polling a previous coordinator.
                coordinators = {}
                for entity_id in mapping.values():
                    component = hass.data[DATA_INSTANCES].get(entity_id.split(".")[0])
                    entity = component.get_entity(entity_id) if component else None
                    coordinator = getattr(entity, "coordinator", None)
                    if coordinator is not None:
                        coordinators[id(coordinator)] = coordinator
                for coordinator in coordinators.values():
                    await coordinator.async_refresh()
                await hass.async_block_till_done()
                values = {key: (state.state if (state := hass.states.get(entity_id)) else None)
                          for key, entity_id in mapping.items()}
                if all(value not in (None, "unknown", "unavailable") for value in values.values()):
                    return values
                await asyncio.sleep(.1)
    except TimeoutError as err:
        raise RuntimeError(f"KORE feedback did not become ready: {values}") from err
