"""Schedule Time entities for Multi-Group Boiler Heating."""

from __future__ import annotations

from datetime import time

from homeassistant.components.time import TimeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN, SCHEDULE_SLOTS
from .coordinator import MultiGroupHeatingController


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the 4 daily schedule time entities."""
    controller: MultiGroupHeatingController = hass.data[DOMAIN][entry.entry_id]
    entities = [
        HeatingScheduleTimeEntity(controller, slot_id, label)
        for slot_id, label, _default_hm, _target_mode in SCHEDULE_SLOTS
    ]
    async_add_entities(entities, True)


class HeatingScheduleTimeEntity(TimeEntity, RestoreEntity):
    """Configurable daily transition time for Multi-Group Boiler Heating."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:clock-outline"
    _attr_should_poll = False

    def __init__(
        self,
        controller: MultiGroupHeatingController,
        slot_id: str,
        label: str,
    ) -> None:
        """Initialize the schedule time entity."""
        self._controller = controller
        self._slot_id = slot_id
        self._attr_name = label
        self._attr_unique_id = f"{controller.entry.entry_id}_time_{slot_id}"
        self._attr_device_info = controller.device_info

    @property
    def native_value(self) -> time:
        """Return the current transition time."""
        return self._controller.schedule_times[self._slot_id]

    async def async_added_to_hass(self) -> None:
        """Restore persisted schedule time on startup."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state not in (
            "unknown",
            "unavailable",
        ):
            try:
                parsed = time.fromisoformat(last_state.state)
                self._controller.update_schedule_time(self._slot_id, parsed)
            except ValueError:
                pass

    async def async_set_value(self, value: time) -> None:
        """Update the transition time and re-register daily schedule listener."""
        self._controller.update_schedule_time(self._slot_id, value)
        self.async_write_ha_state()
