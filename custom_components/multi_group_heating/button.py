"""Override / bypass buttons for Multi-Group Boiler Heating."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import MultiGroupHeatingController


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up bypass buttons for anti-cycle cooldown and single-TRV partner wait."""
    controller: MultiGroupHeatingController = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            CancelAntiCycleCooldownButton(controller),
            FinishSingleTrvWaitButton(controller),
        ]
    )


class CancelAntiCycleCooldownButton(ButtonEntity):
    """Button to immediately cancel Stage 1 boiler anti-short-cycle cooldown."""

    _attr_has_entity_name = True
    _attr_name = "Cancel Anti-Cycle Cooldown"
    _attr_icon = "mdi:timer-cancel-outline"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, controller: MultiGroupHeatingController) -> None:
        """Initialize the button."""
        self._controller = controller
        self._attr_unique_id = (
            f"{controller.entry.entry_id}_cancel_anti_cycle_cooldown"
        )
        self._attr_device_info = controller.device_info

    async def async_press(self) -> None:
        """Cancel cooldown and reconcile boiler immediately."""
        await self._controller.async_cancel_anti_cycle_cooldown()


class FinishSingleTrvWaitButton(ButtonEntity):
    """Button to immediately bypass Stage 2 single-TRV partner wait and start heating."""

    _attr_has_entity_name = True
    _attr_name = "Start Single-TRV Heating Now"
    _attr_icon = "mdi:timer-play-outline"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, controller: MultiGroupHeatingController) -> None:
        """Initialize the button."""
        self._controller = controller
        self._attr_unique_id = (
            f"{controller.entry.entry_id}_finish_single_trv_wait"
        )
        self._attr_device_info = controller.device_info

    async def async_press(self) -> None:
        """Finish partner wait and reconcile boiler immediately."""
        await self._controller.async_finish_single_trv_wait()

