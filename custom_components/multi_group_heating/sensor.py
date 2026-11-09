"""Boiler Demand Action sensor for Multi-Group Boiler Heating."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event

from .const import DOMAIN, STATE_KEEP, STATE_START, STATE_STOP
from .coordinator import MultiGroupHeatingController


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Boiler Demand Action sensor."""
    controller: MultiGroupHeatingController = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([BoilerDemandActionSensor(controller)], True)


class BoilerDemandActionSensor(SensorEntity):
    """Sensor that evaluates start / keep / stop boiler demand across all groups' TRVs."""

    _attr_has_entity_name = True
    _attr_name = "Heating Demand Action"
    _attr_icon = "mdi:fire-circle"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [STATE_START, STATE_KEEP, STATE_STOP]
    _attr_should_poll = False

    def __init__(self, controller: MultiGroupHeatingController) -> None:
        """Initialize the sensor."""
        self._controller = controller
        self._attr_unique_id = (
            f"{controller.entry.entry_id}_heating_demand_action"
        )
        self._attr_device_info = controller.device_info
        self._attr_native_value = STATE_STOP
        self._attr_extra_state_attributes: dict[str, Any] = {}

    async def async_added_to_hass(self) -> None:
        """Subscribe to TRV state changes across all configured groups."""
        self._recalculate()
        trvs = self._controller.all_trvs
        if trvs:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass,
                    trvs,
                    self._async_on_trv_state_change,
                )
            )

    @callback
    def _async_on_trv_state_change(
        self, _event: Event[EventStateChangedData]
    ) -> None:
        """Recalculate boiler demand and drive boiler when any TRV reports telemetry."""
        self._recalculate()
        self.async_write_ha_state()
        self.hass.async_create_task(
            self._controller.async_apply_boiler_action(
                str(self._attr_native_value)
            )
        )

    @callback
    def _recalculate(self) -> None:
        """Evaluate current demand action and attributes from the controller."""
        action, attrs = self._controller.evaluate_boiler_demand()
        self._attr_native_value = action
        self._attr_extra_state_attributes = attrs
