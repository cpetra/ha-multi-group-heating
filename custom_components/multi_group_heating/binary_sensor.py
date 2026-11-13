"""Urgent Heating Demand binary sensor for Multi-Group Boiler Heating."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import MultiGroupHeatingController


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Urgent Heating Demand binary sensor."""
    controller: MultiGroupHeatingController = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([UrgentHeatingDemandBinarySensor(controller)], True)


class UrgentHeatingDemandBinarySensor(BinarySensorEntity):
    """Binary sensor indicating multi-TRV, cold-weather, or deep-deficit heating demand."""

    _attr_has_entity_name = True
    _attr_name = "Urgent Heating Demand"
    _attr_icon = "mdi:fire-alert"
    _attr_should_poll = False

    def __init__(self, controller: MultiGroupHeatingController) -> None:
        """Initialize the urgent heating demand binary sensor."""
        self._controller = controller
        self._attr_unique_id = (
            f"{controller.entry.entry_id}_urgent_heating_demand"
        )
        self._attr_device_info = controller.device_info
        self._attr_is_on = False
        self._attr_extra_state_attributes: dict[str, Any] = {}

    async def async_added_to_hass(self) -> None:
        """Subscribe to controller demand updates."""
        self._recalculate()
        self.async_on_remove(
            self._controller.add_demand_listener(self._handle_update)
        )

    @callback
    def _handle_update(self) -> None:
        """Refresh state and write to HA."""
        self._recalculate()
        self.async_write_ha_state()

    @callback
    def _recalculate(self) -> None:
        """Evaluate urgent demand flags from the controller."""
        _action, attrs = self._controller.evaluate_boiler_demand()
        self._attr_is_on = bool(attrs["urgent_demand"])
        self._attr_extra_state_attributes = {
            "urgent_reasons": attrs["urgent_reasons"],
            "heating_count": attrs["heating_count"],
            "max_calling_deficit": attrs["max_calling_deficit"],
            "outdoor_temperature": attrs["outdoor_temperature"],
            "cold_weather_threshold": self._controller.cold_weather_threshold,
            "urgent_deficit_threshold": self._controller.urgent_deficit_threshold,
        }

