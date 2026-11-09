"""Heating Mode Select entity for Multi-Group Boiler Heating."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN, HEATING_MODES
from .coordinator import MultiGroupHeatingController


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Heating Mode select entity."""
    controller: MultiGroupHeatingController = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([HeatingModeSelect(controller)], True)


class HeatingModeSelect(SelectEntity, RestoreEntity):
    """Representation of the global heating schedule mode (Away, Comfort, Day, Night)."""

    _attr_has_entity_name = True
    _attr_name = "Heating Mode"
    _attr_icon = "mdi:home-thermometer-outline"
    _attr_options = HEATING_MODES
    _attr_should_poll = False

    def __init__(self, controller: MultiGroupHeatingController) -> None:
        """Initialize the Heating Mode select entity."""
        self._controller = controller
        self._attr_unique_id = f"{controller.entry.entry_id}_heating_mode"
        self._attr_device_info = controller.device_info

    @property
    def current_option(self) -> str:
        """Return the current selected mode."""
        return self._controller.mode

    async def async_added_to_hass(self) -> None:
        """Restore previous mode on startup without overwriting manual TRV overrides."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state in HEATING_MODES:
            await self._controller.async_set_mode(
                last_state.state, apply_presets=False
            )
        self.async_on_remove(
            self._controller.add_mode_listener(self._handle_controller_update)
        )

    @callback
    def _handle_controller_update(self) -> None:
        """Write state when mode changes from schedule timer."""
        self.async_write_ha_state()

    async def async_select_option(self, option: str) -> None:
        """Change the selected mode and push preset targets to all groups."""
        await self._controller.async_set_mode(option, apply_presets=True)

