"""Boiler Demand, Open TRVs Count, and Controller State sensors for Multi-Group Boiler Heating."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONTROLLER_STATES,
    CONTROLLER_STATE_IDLE,
    DOMAIN,
    STATE_START,
    STATE_STOP,
)
from .coordinator import MultiGroupHeatingController


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Boiler Demand, Open TRVs Count, and Controller State sensors."""
    controller: MultiGroupHeatingController = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            BoilerDemandActionSensor(controller),
            OpenTrvsCountSensor(controller),
            BoilerControllerStateSensor(controller),
        ],
        True,
    )


class _BaseDemandListenerSensor(SensorEntity):
    """Base sensor subscribed to controller demand and batching updates."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, controller: MultiGroupHeatingController) -> None:
        """Initialize base demand sensor."""
        self._controller = controller
        self._attr_device_info = controller.device_info

    async def async_added_to_hass(self) -> None:
        """Subscribe to controller updates."""
        self._recalculate()
        self.async_on_remove(
            self._controller.add_demand_listener(self._handle_update)
        )

    @callback
    def _handle_update(self) -> None:
        """Refresh sensor state and write to HA."""
        self._recalculate()
        self.async_write_ha_state()

    @callback
    def _recalculate(self) -> None:
        """Recalculate sensor state (implemented by subclasses)."""


class BoilerDemandActionSensor(_BaseDemandListenerSensor):
    """Sensor that evaluates raw start / stop boiler demand from TRV hvac_action ('heating')."""

    _attr_name = "Heating Demand Action"
    _attr_icon = "mdi:fire-circle"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [STATE_START, STATE_STOP]

    def __init__(self, controller: MultiGroupHeatingController) -> None:
        """Initialize the demand action sensor."""
        super().__init__(controller)
        self._attr_unique_id = (
            f"{controller.entry.entry_id}_heating_demand_action"
        )
        self._attr_native_value = STATE_STOP
        self._attr_extra_state_attributes: dict[str, Any] = {}

    @callback
    def _recalculate(self) -> None:
        """Evaluate current demand action and attributes from the controller."""
        action, attrs = self._controller.evaluate_boiler_demand()
        self._attr_native_value = action
        self._attr_extra_state_attributes = attrs


class OpenTrvsCountSensor(_BaseDemandListenerSensor):
    """Count of active ('heat') TRVs currently calling for heat ('hvac_action == heating')."""

    _attr_name = "Open TRVs Count"
    _attr_icon = "mdi:valve-open"
    _attr_native_unit_of_measurement = "TRVs"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0

    def __init__(self, controller: MultiGroupHeatingController) -> None:
        """Initialize the open TRVs count sensor."""
        super().__init__(controller)
        self._attr_unique_id = f"{controller.entry.entry_id}_open_trvs_count"
        self._attr_native_value = 0
        self._attr_extra_state_attributes: dict[str, Any] = {}

    @callback
    def _recalculate(self) -> None:
        """Update count of open TRVs."""
        _action, attrs = self._controller.evaluate_boiler_demand()
        self._attr_native_value = int(attrs["heating_count"])
        self._attr_extra_state_attributes = {
            "heating_trvs": attrs["heating_trvs"],
            "idle_trvs": attrs["idle_trvs"],
            "max_calling_deficit": attrs["max_calling_deficit"],
        }


class BoilerControllerStateSensor(_BaseDemandListenerSensor):
    """Two-stage load-aware boiler controller state (idle, cooldown_hold, partner_wait_hold, heating)."""

    _attr_name = "Boiler Controller State"
    _attr_icon = "mdi:water-boiler-auto"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = CONTROLLER_STATES

    def __init__(self, controller: MultiGroupHeatingController) -> None:
        """Initialize the boiler controller state sensor."""
        super().__init__(controller)
        self._attr_unique_id = (
            f"{controller.entry.entry_id}_boiler_controller_state"
        )
        self._attr_native_value = CONTROLLER_STATE_IDLE
        self._attr_extra_state_attributes: dict[str, Any] = {}

    @callback
    def _recalculate(self) -> None:
        """Update controller state and timer metadata."""
        _action, attrs = self._controller.evaluate_boiler_demand()
        self._attr_native_value = self._controller.controller_state
        self._attr_extra_state_attributes = {
            "cooldown_active": attrs["cooldown_active"],
            "cooldown_ends_at": attrs["cooldown_ends_at"],
            "partner_wait_active": attrs["partner_wait_active"],
            "partner_wait_ends_at": attrs["partner_wait_ends_at"],
            "urgent_demand": attrs["urgent_demand"],
            "urgent_reasons": attrs["urgent_reasons"],
            "boiler_physically_heating": attrs["boiler_physically_heating"],
        }
