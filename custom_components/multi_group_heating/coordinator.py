"""Central 3-tier controller for Multi-Group Boiler Heating."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, time, timedelta
import logging
from typing import Any

from homeassistant.components.climate import (
    ATTR_CURRENT_TEMPERATURE,
    ATTR_HVAC_ACTION,
    ATTR_HVAC_MODE,
    DOMAIN as CLIMATE_DOMAIN,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_TEMPERATURE,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_TEMPERATURE,
    EVENT_HOMEASSISTANT_STARTED,
)
from homeassistant.core import (
    CALLBACK_TYPE,
    Event,
    EventStateChangedData,
    HomeAssistant,
    State,
    callback,
)
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_change,
    async_track_time_interval,
)
from homeassistant.util import dt as dt_util

from .const import (
    BOILER_WATCHDOG_INTERVAL_SECONDS,
    CONF_AUTO_CONTROL_BOILER,
    CONF_BOILER_ENTITY,
    CONF_BOILER_FIRING_TEMP,
    CONF_COLD_WEATHER_THRESHOLD,
    CONF_OUTDOOR_TEMP_ENTITY,
    CONF_URGENT_DEFICIT_THRESHOLD,
    CONF_ZIGBEE_PACING_DELAY,
    CONF_ZIGBEE_RETRY_DELAY,
    CONTROLLER_STATE_COOLDOWN,
    CONTROLLER_STATE_HEATING,
    CONTROLLER_STATE_IDLE,
    CONTROLLER_STATE_PARTNER_WAIT,
    DEFAULT_AUTO_CONTROL_BOILER,
    DEFAULT_BOILER_FIRING_TEMP,
    DEFAULT_BOILER_MIN_OFF_MINUTES,
    DEFAULT_COLD_WEATHER_THRESHOLD,
    DEFAULT_GROUPS,
    DEFAULT_SINGLE_TRV_WAIT_MINUTES,
    DEFAULT_URGENT_DEFICIT_THRESHOLD,
    DEFAULT_ZIGBEE_PACING_DELAY,
    DEFAULT_ZIGBEE_RETRY_DELAY,
    DOMAIN,
    MODE_AWAY,
    MODE_COMFORT,
    MODE_DAY,
    SCHEDULE_SLOTS,
    STATE_START,
    STATE_STOP,
    USER_INTENT_BYPASS_SECONDS,
)

_LOGGER = logging.getLogger(__name__)


class MultiGroupHeatingController:
    """Coordinates schedule modes, 5 TRV groups, paced Zigbee sync, and two-stage boiler demand."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the controller."""
        self.hass = hass
        self.entry = entry
        cfg = {**entry.data, **entry.options}

        self.boiler_entity: str = cfg[CONF_BOILER_ENTITY]
        boiler_slug = (
            self.boiler_entity.split(".", 1)[1]
            if "." in self.boiler_entity
            else self.boiler_entity
        )
        self.boiler_hvac_action_sensor: str = f"sensor.{boiler_slug}_hvac_action"

        self.boiler_firing_temp: float = float(
            cfg.get(CONF_BOILER_FIRING_TEMP, DEFAULT_BOILER_FIRING_TEMP)
        )
        self.outdoor_temp_entity: str | None = cfg.get(CONF_OUTDOOR_TEMP_ENTITY) or None
        self.cold_weather_threshold: float = float(
            cfg.get(CONF_COLD_WEATHER_THRESHOLD, DEFAULT_COLD_WEATHER_THRESHOLD)
        )
        self.urgent_deficit_threshold: float = float(
            cfg.get(CONF_URGENT_DEFICIT_THRESHOLD, DEFAULT_URGENT_DEFICIT_THRESHOLD)
        )
        self.zigbee_pacing_delay: int = int(
            cfg.get(CONF_ZIGBEE_PACING_DELAY, DEFAULT_ZIGBEE_PACING_DELAY)
        )
        self.zigbee_retry_delay: int = int(
            cfg.get(CONF_ZIGBEE_RETRY_DELAY, DEFAULT_ZIGBEE_RETRY_DELAY)
        )
        self.auto_control_boiler: bool = bool(
            cfg.get(CONF_AUTO_CONTROL_BOILER, DEFAULT_AUTO_CONTROL_BOILER)
        )

        # Runtime tunable batching parameters (restored by number.py entities)
        self.boiler_min_off_minutes: int = DEFAULT_BOILER_MIN_OFF_MINUTES
        self.boiler_single_trv_wait_minutes: int = DEFAULT_SINGLE_TRV_WAIT_MINUTES

        self.mode: str = MODE_DAY
        self.schedule_times: dict[str, time] = {
            slot_id: time(hour=h, minute=m)
            for slot_id, _label, (h, m), _target_mode in SCHEDULE_SLOTS
        }

        self.groups: dict[int, dict[str, Any]] = {}
        self.trv_to_group: dict[str, int] = {}
        for grp in DEFAULT_GROUPS:
            idx = grp["index"]
            name = cfg.get(f"group_{idx}_name") or grp["default_name"]
            trvs = list(cfg.get(f"group_{idx}_trvs", []))
            self.groups[idx] = {
                "index": idx,
                "name": name,
                "trvs": trvs,
                "target": float(grp["presets"][MODE_DAY]),
                "presets": dict(grp["presets"]),
            }
            for trv_id in trvs:
                self.trv_to_group[trv_id] = idx

        # Entity state update listeners
        self._mode_listeners: list[Callable[[], None]] = []
        self._group_target_listeners: dict[int, list[Callable[[], None]]] = {
            i: [] for i in range(1, 6)
        }
        self._batching_config_listeners: list[Callable[[], None]] = []
        self._demand_listeners: list[Callable[[], None]] = []

        # Event / timer unsub handles
        self._schedule_unsubs: dict[str, CALLBACK_TYPE] = {}
        self._state_unsubs: list[CALLBACK_TYPE] = []
        self._trv_wakeup_unsubs: dict[str, CALLBACK_TYPE] = {}
        self._trv_verify_unsubs: dict[str, CALLBACK_TYPE] = {}
        self._boiler_ack_unsubs: list[CALLBACK_TYPE] = []
        self._watchdog_unsub: CALLBACK_TYPE | None = None
        self._startup_unsub: CALLBACK_TYPE | None = None

        # Paced per-TRV Zigbee queue
        self._pending_trv_jobs: dict[str, tuple[float, bool]] = {}
        self._pacing_task: asyncio.Task[None] | None = None

        # Two-stage boiler controller state
        self._boiler_target_heating: bool = False
        self._cooldown_unsub: CALLBACK_TYPE | None = None
        self._cooldown_ends_at: datetime | None = None
        self._partner_wait_unsub: CALLBACK_TYPE | None = None
        self._partner_wait_ends_at: datetime | None = None
        self._partner_wait_satisfied: bool = False
        self._user_intent_until: datetime | None = None

    @property
    def device_info(self) -> DeviceInfo:
        """Return device registry info for the controller."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.entry.entry_id)},
            name=f"Multi-Group Heating ({self.boiler_entity})",
            manufacturer="Costa",
            model="3-Tier Load-Aware Multi-Group TRV & Boiler Controller",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def all_trvs(self) -> list[str]:
        """Return flat list of all configured TRV climate entities across all 5 groups."""
        entities: list[str] = []
        for grp in self.groups.values():
            for trv in grp["trvs"]:
                if trv not in entities:
                    entities.append(trv)
        return entities

    @property
    def is_cooldown_active(self) -> bool:
        """Return True if the Stage 1 anti-short-cycle cooldown timer is active."""
        return (
            self._cooldown_ends_at is not None
            and self._cooldown_ends_at > dt_util.utcnow()
        )

    @property
    def is_partner_wait_active(self) -> bool:
        """Return True if the Stage 2 single-TRV partner wait timer is active."""
        return (
            self._partner_wait_ends_at is not None
            and self._partner_wait_ends_at > dt_util.utcnow()
        )

    @property
    def controller_state(self) -> str:
        """Return the current high-level boiler controller state."""
        action, _ = self.evaluate_boiler_demand()
        if self._boiler_target_heating:
            return CONTROLLER_STATE_HEATING
        if self.is_cooldown_active:
            return CONTROLLER_STATE_COOLDOWN
        if action == STATE_START and self.is_partner_wait_active:
            return CONTROLLER_STATE_PARTNER_WAIT
        return CONTROLLER_STATE_IDLE

    # ---------------------------------------------------------------------
    # Lifecycle
    # ---------------------------------------------------------------------

    @callback
    def start(self) -> None:
        """Start schedule timers, state listeners, and hardware watchdog."""
        self.register_schedule_timers()

        if self.all_trvs:
            self._state_unsubs.append(
                async_track_state_change_event(
                    self.hass,
                    self.all_trvs,
                    self._async_on_trv_state_change,
                )
            )

        watched_aux = [self.boiler_entity, self.boiler_hvac_action_sensor]
        if self.outdoor_temp_entity:
            watched_aux.append(self.outdoor_temp_entity)

        self._state_unsubs.append(
            async_track_state_change_event(
                self.hass,
                watched_aux,
                self._async_on_boiler_or_outdoor_change,
            )
        )

        self._watchdog_unsub = async_track_time_interval(
            self.hass,
            self._async_on_boiler_watchdog,
            timedelta(seconds=BOILER_WATCHDOG_INTERVAL_SECONDS),
        )

        if self.hass.is_running:
            self.hass.async_create_task(self.async_reconcile_boiler())
        else:

            @callback
            def _on_ha_started(_event: Event) -> None:
                self._startup_unsub = None
                self.hass.async_create_task(self.async_reconcile_boiler())

            self._startup_unsub = self.hass.bus.async_listen_once(
                EVENT_HOMEASSISTANT_STARTED, _on_ha_started
            )

    @callback
    def shutdown(self) -> None:
        """Cancel all listeners, tasks, and timers cleanly on unload."""
        self.clear_schedule_timers()

        for unsub in self._state_unsubs:
            unsub()
        self._state_unsubs.clear()

        for unsub in self._trv_wakeup_unsubs.values():
            unsub()
        self._trv_wakeup_unsubs.clear()

        for unsub in self._trv_verify_unsubs.values():
            unsub()
        self._trv_verify_unsubs.clear()

        self._clear_boiler_ack_timers()
        self._cancel_cooldown_timer()
        self._cancel_partner_wait_timer()

        if self._watchdog_unsub is not None:
            self._watchdog_unsub()
            self._watchdog_unsub = None

        if self._startup_unsub is not None:
            self._startup_unsub()
            self._startup_unsub = None

        self._pending_trv_jobs.clear()
        if self._pacing_task is not None and not self._pacing_task.done():
            self._pacing_task.cancel()
        self._pacing_task = None

    # ---------------------------------------------------------------------
    # Subscription Helpers for Platform Entities
    # ---------------------------------------------------------------------

    def add_mode_listener(self, cb: Callable[[], None]) -> CALLBACK_TYPE:
        """Subscribe to mode changes."""
        self._mode_listeners.append(cb)
        return lambda: self._mode_listeners.remove(cb)

    def add_group_target_listener(
        self, group_idx: int, cb: Callable[[], None]
    ) -> CALLBACK_TYPE:
        """Subscribe to virtual group target changes."""
        self._group_target_listeners[group_idx].append(cb)
        return lambda: self._group_target_listeners[group_idx].remove(cb)

    def add_batching_config_listener(
        self, cb: Callable[[], None]
    ) -> CALLBACK_TYPE:
        """Subscribe to boiler batching minute changes."""
        self._batching_config_listeners.append(cb)
        return lambda: self._batching_config_listeners.remove(cb)

    def add_demand_listener(self, cb: Callable[[], None]) -> CALLBACK_TYPE:
        """Subscribe to boiler demand / controller state updates."""
        self._demand_listeners.append(cb)
        return lambda: self._demand_listeners.remove(cb)

    @callback
    def _notify_demand_listeners(self) -> None:
        """Notify all demand, count, urgent, and controller-state entities."""
        for cb in list(self._demand_listeners):
            cb()

    # ---------------------------------------------------------------------
    # Tier 1: Schedule & Mode Manager
    # ---------------------------------------------------------------------

    def register_schedule_timers(self) -> None:
        """Subscribe to daily schedule time triggers."""
        self.clear_schedule_timers()
        for slot_id, _label, _default_hm, target_mode in SCHEDULE_SLOTS:
            t_val = self.schedule_times[slot_id]

            @callback
            def _handle_time(
                _now: datetime,
                _slot: str = slot_id,
                _mode: str = target_mode,
            ) -> None:
                if self.mode == MODE_AWAY:
                    _LOGGER.debug(
                        "Skipping scheduled transition %s -> %s because Away mode is active",
                        _slot,
                        _mode,
                    )
                    return
                self.hass.async_create_task(self.async_set_mode(_mode))

            self._schedule_unsubs[slot_id] = async_track_time_change(
                self.hass,
                _handle_time,
                hour=t_val.hour,
                minute=t_val.minute,
                second=0,
            )

    def clear_schedule_timers(self) -> None:
        """Cancel all schedule time listeners."""
        for unsub in self._schedule_unsubs.values():
            unsub()
        self._schedule_unsubs.clear()

    def update_schedule_time(self, slot_id: str, new_time: time) -> None:
        """Update a schedule slot time and re-bind timers."""
        self.schedule_times[slot_id] = new_time
        if self._schedule_unsubs:
            self.register_schedule_timers()

    async def async_set_mode(
        self, new_mode: str, apply_presets: bool = True
    ) -> None:
        """Transition heating mode and push preset targets to all groups."""
        self.mode = new_mode
        for cb in list(self._mode_listeners):
            cb()

        if not apply_presets:
            return

        if new_mode == MODE_COMFORT:
            self._trigger_user_intent_bypass("comfort_mode")

        for idx, grp in self.groups.items():
            preset_val = grp["presets"].get(new_mode)
            if preset_val is not None:
                await self.async_set_group_target(
                    idx,
                    float(preset_val),
                    push_to_trvs=True,
                    force_push=True,
                )

        await self.async_reconcile_boiler()

    async def async_set_group_preset(
        self, group_idx: int, mode_key: str, value: float
    ) -> None:
        """Update a group's preset temperature; if that mode is active, update its group target."""
        self.groups[group_idx]["presets"][mode_key] = value
        if self.mode == mode_key:
            await self.async_set_group_target(
                group_idx, value, push_to_trvs=True
            )

    # ---------------------------------------------------------------------
    # Tier 2: Group-to-TRV Binding, Paced Queue & Physical Eco Verification
    # ---------------------------------------------------------------------

    async def async_set_group_target(
        self,
        group_idx: int,
        value: float,
        push_to_trvs: bool = True,
        force_push: bool = False,
    ) -> None:
        """Set a group's virtual target temperature and propagate to active ('heat') TRVs."""
        grp = self.groups[group_idx]
        prev = float(grp["target"])
        grp["target"] = value
        for cb in list(self._group_target_listeners[group_idx]):
            cb()

        if push_to_trvs and value > prev + 0.05:
            self._trigger_user_intent_bypass("group_target_raised")

        if not push_to_trvs or not grp["trvs"]:
            return

        target_changed = abs(prev - value) > 0.01
        if not target_changed and not force_push:
            return

        for entity_id in grp["trvs"]:
            st = self.hass.states.get(entity_id)
            if st is None or st.state != HVACMode.HEAT:
                continue
            # Always push to all active TRVs when the group target changed so ZHA's
            # optimistic setpoint cache never skips a sleepy TRV; on same-target
            # mode boundaries (force_push), sync any TRV that was manually overridden
            # or physically out of sync with Eco hysteresis.
            if target_changed or self._trv_needs_physical_sync(st, value):
                self._enqueue_trv_setpoint(entity_id, value, verify_after=True)

        if push_to_trvs and value > prev + 0.05:
            await self.async_reconcile_boiler()

    @staticmethod
    def _trv_needs_physical_sync(state: State, target_temp: float) -> bool:
        """Check both setpoint attribute and physical Eco valve action against target."""
        if state.state != HVACMode.HEAT:
            return False

        cur_sp_raw = state.attributes.get(ATTR_TEMPERATURE)
        try:
            cur_sp = float(cur_sp_raw)
        except (TypeError, ValueError):
            return True

        if abs(cur_sp - target_temp) > 0.05:
            return True

        cur_temp_raw = state.attributes.get(ATTR_CURRENT_TEMPERATURE)
        hvac_act = state.attributes.get(ATTR_HVAC_ACTION, HVACAction.IDLE)
        try:
            cur_temp = float(cur_temp_raw)
        except (TypeError, ValueError):
            return False

        # Eco hardware hysteresis opens 100% ('heating') when target - current >= 0.5°C
        # and closes 0% ('idle') when current >= target. If physical action contradicts
        # this, ZHA optimistically cached the setpoint while the sleepy TRV dropped it.
        if (target_temp - cur_temp) >= 0.55 and hvac_act != HVACAction.HEATING:
            return True
        if (cur_temp - target_temp) >= 0.1 and hvac_act == HVACAction.HEATING:
            return True

        return False

    @callback
    def _enqueue_trv_setpoint(
        self, entity_id: str, target_temp: float, verify_after: bool = True
    ) -> None:
        """Queue a single-TRV setpoint write for paced execution."""
        existing = self._pending_trv_jobs.get(entity_id)
        keep_verify = verify_after or (existing[1] if existing else False)
        self._pending_trv_jobs[entity_id] = (target_temp, keep_verify)

        if self._pacing_task is None or self._pacing_task.done():
            self._pacing_task = self.hass.async_create_task(
                self._async_process_trv_queue()
            )

    async def _async_process_trv_queue(self) -> None:
        """Send climate.set_temperature one TRV at a time to prevent Zigbee router buffer overflow."""
        try:
            while self._pending_trv_jobs:
                entity_id = next(iter(self._pending_trv_jobs))
                target_temp, verify_after = self._pending_trv_jobs.pop(entity_id)

                st = self.hass.states.get(entity_id)
                if st is None or st.state != HVACMode.HEAT:
                    continue

                # Omit hvac_mode to prevent Tuya MCU 20.0°C setpoint reset bounce
                await self.hass.services.async_call(
                    CLIMATE_DOMAIN,
                    SERVICE_SET_TEMPERATURE,
                    {
                        ATTR_ENTITY_ID: entity_id,
                        ATTR_TEMPERATURE: target_temp,
                    },
                    blocking=False,
                )

                if verify_after and self.zigbee_retry_delay > 0:
                    self._schedule_trv_verification(entity_id, target_temp)

                if self._pending_trv_jobs and self.zigbee_pacing_delay > 0:
                    await asyncio.sleep(self.zigbee_pacing_delay)
        except asyncio.CancelledError:
            return

    @callback
    def _schedule_trv_verification(
        self, entity_id: str, target_temp: float
    ) -> None:
        """Verify physical Eco response after zigbee_retry_delay and retry once if missed."""
        old_unsub = self._trv_verify_unsubs.pop(entity_id, None)
        if old_unsub is not None:
            old_unsub()

        @callback
        def _verify_callback(_now: datetime) -> None:
            self._trv_verify_unsubs.pop(entity_id, None)
            st = self.hass.states.get(entity_id)
            if st is None or st.state != HVACMode.HEAT:
                return
            if self._trv_needs_physical_sync(st, target_temp):
                _LOGGER.debug(
                    "Physical Eco/setpoint mismatch on %s after %ss; queueing paced retry to %.1f °C",
                    entity_id,
                    self.zigbee_retry_delay,
                    target_temp,
                )
                self._enqueue_trv_setpoint(
                    entity_id, target_temp, verify_after=False
                )

        self._trv_verify_unsubs[entity_id] = async_call_later(
            self.hass,
            self.zigbee_retry_delay,
            _verify_callback,
        )

    @callback
    def _async_on_trv_state_change(
        self, event: Event[EventStateChangedData]
    ) -> None:
        """Handle TRV off->heat wakeup sync, manual setpoint raises, rogue heating, and boiler demand."""
        entity_id = event.data["entity_id"]
        old_state = event.data["old_state"]
        new_state = event.data["new_state"]

        if new_state is not None and new_state.state == HVACMode.HEAT:
            # 1. TRV turned ON (off -> heat): wait 2s for Tuya MCU init, then sync to group target
            if old_state is not None and old_state.state == HVACMode.OFF:
                self._trigger_user_intent_bypass("trv_turned_on")
                grp_idx = self.trv_to_group.get(entity_id)
                if grp_idx is not None:
                    prev_wakeup = self._trv_wakeup_unsubs.pop(entity_id, None)
                    if prev_wakeup is not None:
                        prev_wakeup()

                    @callback
                    def _sync_woken_trv(
                        _now: datetime,
                        _eid: str = entity_id,
                        _gidx: int = grp_idx,
                    ) -> None:
                        self._trv_wakeup_unsubs.pop(_eid, None)
                        target = float(self.groups[_gidx]["target"])
                        self._enqueue_trv_setpoint(_eid, target, verify_after=True)

                    self._trv_wakeup_unsubs[entity_id] = async_call_later(
                        self.hass, 2.0, _sync_woken_trv
                    )

            # 2. Setpoint raised on an active TRV -> instant boiler cooldown/wait bypass
            if old_state is not None and old_state.state == HVACMode.HEAT:
                try:
                    old_sp = float(old_state.attributes.get(ATTR_TEMPERATURE))
                    new_sp = float(new_state.attributes.get(ATTR_TEMPERATURE))
                    if new_sp > old_sp + 0.05:
                        self._trigger_user_intent_bypass("trv_setpoint_raised")
                except (TypeError, ValueError):
                    pass

            # 3. Rogue-heating self-healing: TRV enters 'heating' while current_temp >= setpoint
            old_act = (
                old_state.attributes.get(ATTR_HVAC_ACTION)
                if old_state is not None
                else None
            )
            new_act = new_state.attributes.get(ATTR_HVAC_ACTION)
            if (
                new_act == HVACAction.HEATING
                and old_act != HVACAction.HEATING
                and entity_id not in self._pending_trv_jobs
            ):
                try:
                    cur_temp = float(
                        new_state.attributes.get(ATTR_CURRENT_TEMPERATURE)
                    )
                    cur_sp = float(new_state.attributes.get(ATTR_TEMPERATURE))
                    if (cur_temp - cur_sp) >= 0.0:
                        _LOGGER.info(
                            "Rogue heating detected on %s (current %.1f °C >= target %.1f °C); re-pushing setpoint while radio is awake",
                            entity_id,
                            cur_temp,
                            cur_sp,
                        )
                        self._enqueue_trv_setpoint(
                            entity_id, cur_sp, verify_after=True
                        )
                except (TypeError, ValueError):
                    pass

        self._notify_demand_listeners()
        self.hass.async_create_task(self.async_reconcile_boiler())

    # ---------------------------------------------------------------------
    # Tier 3: Two-Stage Load-Aware Boiler Demand & Physical Relay ACK
    # ---------------------------------------------------------------------

    async def async_set_boiler_min_off_minutes(
        self, minutes: int, reconcile: bool = True
    ) -> None:
        """Update Stage 1 anti-cycle cooldown duration."""
        self.boiler_min_off_minutes = max(0, int(minutes))
        for cb in list(self._batching_config_listeners):
            cb()

        if self.boiler_min_off_minutes <= 0 and self.is_cooldown_active:
            self._cancel_cooldown_timer()

        self._notify_demand_listeners()
        if reconcile:
            await self.async_reconcile_boiler()

    async def async_set_single_trv_wait_minutes(
        self, minutes: int, reconcile: bool = True
    ) -> None:
        """Update Stage 2 single-TRV partner wait duration."""
        self.boiler_single_trv_wait_minutes = max(0, int(minutes))
        for cb in list(self._batching_config_listeners):
            cb()

        if (
            self.boiler_single_trv_wait_minutes <= 0
            and self.is_partner_wait_active
        ):
            self._cancel_partner_wait_timer()

        self._notify_demand_listeners()
        if reconcile:
            await self.async_reconcile_boiler()

    async def async_cancel_anti_cycle_cooldown(self) -> None:
        """Manually cancel active anti-cycle cooldown from UI button."""
        self._cancel_cooldown_timer()
        self._trigger_user_intent_bypass("manual_cancel_cooldown")
        self._notify_demand_listeners()
        await self.async_reconcile_boiler()

    async def async_finish_single_trv_wait(self) -> None:
        """Manually bypass single-TRV partner wait and fire boiler now."""
        self._cancel_partner_wait_timer()
        self._partner_wait_satisfied = True
        self._trigger_user_intent_bypass("manual_finish_partner_wait")
        self._notify_demand_listeners()
        await self.async_reconcile_boiler()

    @callback
    def _trigger_user_intent_bypass(self, reason: str) -> None:
        """Cancel active holds and open a short intent window so waking Zigbee TRVs fire the boiler immediately."""
        _LOGGER.debug("User/schedule intent bypass triggered (%s)", reason)
        self._user_intent_until = dt_util.utcnow() + timedelta(
            seconds=USER_INTENT_BYPASS_SECONDS
        )
        self._cancel_cooldown_timer()
        self._cancel_partner_wait_timer()

    def _is_user_intent_active(self) -> bool:
        """Return True if within the recent user/schedule intent bypass window."""
        return (
            self._user_intent_until is not None
            and self._user_intent_until > dt_util.utcnow()
        )

    def _get_outdoor_temperature(self) -> float | None:
        """Read outdoor temperature from configured sensor or weather entity."""
        if not self.outdoor_temp_entity:
            return None
        st = self.hass.states.get(self.outdoor_temp_entity)
        if st is None or st.state in ("unavailable", "unknown"):
            return None
        raw = (
            st.attributes.get(ATTR_TEMPERATURE)
            if self.outdoor_temp_entity.startswith("weather.")
            else st.state
        )
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    def evaluate_boiler_demand(self) -> tuple[str, dict[str, Any]]:
        """Evaluate raw demand, open TRVs count, and urgent demand criteria."""
        active: list[str] = []
        heating_trvs: list[str] = []
        idle_trvs: list[str] = []
        max_deficit = 0.0

        for entity_id in self.all_trvs:
            state = self.hass.states.get(entity_id)
            if state is None or state.state != HVACMode.HEAT:
                continue
            active.append(entity_id)
            if state.attributes.get(ATTR_HVAC_ACTION) == HVACAction.HEATING:
                heating_trvs.append(entity_id)
                try:
                    sp = float(state.attributes.get(ATTR_TEMPERATURE))
                    cur = float(state.attributes.get(ATTR_CURRENT_TEMPERATURE))
                    deficit = round(sp - cur, 2)
                    if deficit > max_deficit:
                        max_deficit = deficit
                except (TypeError, ValueError):
                    pass
            else:
                idle_trvs.append(entity_id)

        open_count = len(heating_trvs)
        action = STATE_START if open_count > 0 else STATE_STOP
        outdoor_temp = self._get_outdoor_temperature()

        urgent_reasons: list[str] = []
        if open_count > 0:
            if open_count >= 2:
                urgent_reasons.append("multi_trv")
            if (
                outdoor_temp is not None
                and outdoor_temp <= self.cold_weather_threshold
            ):
                urgent_reasons.append("cold_weather")
            if max_deficit >= self.urgent_deficit_threshold:
                urgent_reasons.append("deep_deficit")
            if self.boiler_single_trv_wait_minutes <= 0:
                urgent_reasons.append("partner_wait_disabled")

        attrs: dict[str, Any] = {
            "boiler_entity": self.boiler_entity,
            "boiler_firing_temp": self.boiler_firing_temp,
            "auto_control_boiler": self.auto_control_boiler,
            "active_count": len(active),
            "heating_count": open_count,
            "idle_count": len(idle_trvs),
            "active_trvs": active,
            "heating_trvs": heating_trvs,
            "idle_trvs": idle_trvs,
            "max_calling_deficit": max_deficit,
            "outdoor_temperature": outdoor_temp,
            "urgent_demand": len(urgent_reasons) > 0,
            "urgent_reasons": urgent_reasons,
            "cooldown_active": self.is_cooldown_active,
            "cooldown_ends_at": (
                self._cooldown_ends_at.isoformat()
                if self.is_cooldown_active and self._cooldown_ends_at
                else None
            ),
            "partner_wait_active": self.is_partner_wait_active,
            "partner_wait_ends_at": (
                self._partner_wait_ends_at.isoformat()
                if self.is_partner_wait_active and self._partner_wait_ends_at
                else None
            ),
            "boiler_physically_heating": self._is_boiler_physically_heating(),
        }
        return action, attrs

    async def async_reconcile_boiler(self) -> None:
        """Run the two-stage load-aware state machine and drive the boiler thermostat."""
        action, attrs = self.evaluate_boiler_demand()
        urgent_demand = bool(attrs["urgent_demand"])
        bypass_now = self._is_user_intent_active()

        if bypass_now:
            self._cancel_cooldown_timer()
            self._cancel_partner_wait_timer()

        if action == STATE_STOP or urgent_demand:
            self._cancel_partner_wait_timer()

        if action == STATE_STOP:
            was_running = (
                self._boiler_target_heating
                or self._is_boiler_physically_heating()
            )
            self._boiler_target_heating = False
            self._partner_wait_satisfied = False

            if (
                was_running
                and self.boiler_min_off_minutes > 0
                and not self.is_cooldown_active
            ):
                self._start_cooldown_timer(self.boiler_min_off_minutes * 60)

            await self._async_ensure_boiler_off()
            self._notify_demand_listeners()
            return

        # action == STATE_START
        if self.is_cooldown_active and not bypass_now:
            self._boiler_target_heating = False
            await self._async_ensure_boiler_off()
            self._notify_demand_listeners()
            return

        should_fire = (
            self._boiler_target_heating
            or self._is_boiler_physically_heating()
            or urgent_demand
            or self._partner_wait_satisfied
            or bypass_now
            or self.boiler_single_trv_wait_minutes <= 0
        )

        if should_fire:
            self._cancel_partner_wait_timer()
            self._boiler_target_heating = True
            self._user_intent_until = None
            await self._async_ensure_boiler_heating()
        else:
            self._boiler_target_heating = False
            if not self.is_partner_wait_active:
                self._start_partner_wait_timer(
                    self.boiler_single_trv_wait_minutes * 60
                )

        self._notify_demand_listeners()

    # ---------------------------------------------------------------------
    # Stage 1 & Stage 2 Timers
    # ---------------------------------------------------------------------

    @callback
    def _start_cooldown_timer(self, duration_seconds: int) -> None:
        """Start Stage 1 anti-short-cycle cooldown timer."""
        self._cancel_cooldown_timer()
        self._cooldown_ends_at = dt_util.utcnow() + timedelta(
            seconds=duration_seconds
        )

        @callback
        def _on_cooldown_done(_now: datetime) -> None:
            self._cooldown_unsub = None
            self._cooldown_ends_at = None
            self._notify_demand_listeners()
            self.hass.async_create_task(self.async_reconcile_boiler())

        self._cooldown_unsub = async_call_later(
            self.hass, duration_seconds, _on_cooldown_done
        )

    @callback
    def _cancel_cooldown_timer(self) -> None:
        """Cancel Stage 1 anti-short-cycle cooldown timer."""
        if self._cooldown_unsub is not None:
            self._cooldown_unsub()
            self._cooldown_unsub = None
        self._cooldown_ends_at = None

    @callback
    def _start_partner_wait_timer(self, duration_seconds: int) -> None:
        """Start Stage 2 single-TRV partner wait timer."""
        self._cancel_partner_wait_timer()
        self._partner_wait_ends_at = dt_util.utcnow() + timedelta(
            seconds=duration_seconds
        )

        @callback
        def _on_partner_wait_done(_now: datetime) -> None:
            self._partner_wait_unsub = None
            self._partner_wait_ends_at = None
            self._partner_wait_satisfied = True
            self._notify_demand_listeners()
            self.hass.async_create_task(self.async_reconcile_boiler())

        self._partner_wait_unsub = async_call_later(
            self.hass, duration_seconds, _on_partner_wait_done
        )

    @callback
    def _cancel_partner_wait_timer(self) -> None:
        """Cancel Stage 2 single-TRV partner wait timer."""
        if self._partner_wait_unsub is not None:
            self._partner_wait_unsub()
            self._partner_wait_unsub = None
        self._partner_wait_ends_at = None

    # ---------------------------------------------------------------------
    # Physical Boiler Hardware Control, ACK Retry & Watchdog
    # ---------------------------------------------------------------------

    def _get_boiler_hvac_action(self) -> str | None:
        """Return physical boiler hvac_action from companion ZHA sensor or climate attribute."""
        companion = self.hass.states.get(self.boiler_hvac_action_sensor)
        if companion is not None and companion.state not in (
            "unavailable",
            "unknown",
        ):
            return companion.state
        boiler_st = self.hass.states.get(self.boiler_entity)
        if boiler_st is not None:
            act = boiler_st.attributes.get(ATTR_HVAC_ACTION)
            if act is not None:
                return str(act)
        return None

    def _is_boiler_physically_off(self) -> bool:
        """Verify both climate state == 'off' and physical relay hvac_action != 'heating'."""
        boiler_st = self.hass.states.get(self.boiler_entity)
        if boiler_st is None or boiler_st.state in ("unavailable", "unknown"):
            return False
        if boiler_st.state != HVACMode.OFF:
            return False
        hvac_act = self._get_boiler_hvac_action()
        if hvac_act == HVACAction.HEATING:
            return False
        return True

    def _is_boiler_physically_heating(self) -> bool:
        """Verify climate state == 'heat', setpoint matches firing temp, and physical relay is heating."""
        boiler_st = self.hass.states.get(self.boiler_entity)
        if boiler_st is None or boiler_st.state != HVACMode.HEAT:
            return False

        cur_sp = boiler_st.attributes.get(ATTR_TEMPERATURE)
        if cur_sp is not None:
            try:
                if abs(float(cur_sp) - self.boiler_firing_temp) > 0.2:
                    return False
            except (TypeError, ValueError):
                pass

        hvac_act = self._get_boiler_hvac_action()
        if hvac_act is not None and hvac_act != HVACAction.HEATING:
            cur_temp = boiler_st.attributes.get(ATTR_CURRENT_TEMPERATURE)
            try:
                if (
                    cur_temp is None
                    or float(cur_temp) < self.boiler_firing_temp - 0.2
                ):
                    return False
            except (TypeError, ValueError):
                return False

        return True

    @callback
    def _clear_boiler_ack_timers(self) -> None:
        """Cancel pending 10s/20s boiler ACK verification callbacks."""
        for unsub in self._boiler_ack_unsubs:
            unsub()
        self._boiler_ack_unsubs.clear()

    async def _async_send_boiler_start(self) -> None:
        """Send heat mode + ceiling temperature to central boiler thermostat."""
        await self.hass.services.async_call(
            CLIMATE_DOMAIN,
            SERVICE_SET_HVAC_MODE,
            {
                ATTR_ENTITY_ID: self.boiler_entity,
                ATTR_HVAC_MODE: HVACMode.HEAT,
            },
            blocking=False,
        )
        await self.hass.services.async_call(
            CLIMATE_DOMAIN,
            SERVICE_SET_TEMPERATURE,
            {
                ATTR_ENTITY_ID: self.boiler_entity,
                ATTR_HVAC_MODE: HVACMode.HEAT,
                ATTR_TEMPERATURE: self.boiler_firing_temp,
            },
            blocking=False,
        )

    async def _async_send_boiler_stop(self) -> None:
        """Send off mode to central boiler thermostat."""
        await self.hass.services.async_call(
            CLIMATE_DOMAIN,
            SERVICE_SET_HVAC_MODE,
            {
                ATTR_ENTITY_ID: self.boiler_entity,
                ATTR_HVAC_MODE: HVACMode.OFF,
            },
            blocking=False,
        )

    async def _async_ensure_boiler_heating(self) -> None:
        """Start boiler if not already physically heating and schedule 10s + 20s ACK retries."""
        if not self.auto_control_boiler or self._is_boiler_physically_heating():
            return

        self._clear_boiler_ack_timers()
        await self._async_send_boiler_start()

        for delay_s in (10.0, 20.0):

            @callback
            def _ack_start_check(_now: datetime) -> None:
                if (
                    self.auto_control_boiler
                    and self._boiler_target_heating
                    and not self._is_boiler_physically_heating()
                ):
                    _LOGGER.warning(
                        "Boiler %s did not confirm physical heating ACK; retrying start command",
                        self.boiler_entity,
                    )
                    self.hass.async_create_task(self._async_send_boiler_start())

            self._boiler_ack_unsubs.append(
                async_call_later(self.hass, delay_s, _ack_start_check)
            )

    async def _async_ensure_boiler_off(self) -> None:
        """Stop boiler if not already physically off and schedule 10s + 20s ACK retries."""
        if not self.auto_control_boiler or self._is_boiler_physically_off():
            return

        self._clear_boiler_ack_timers()
        await self._async_send_boiler_stop()

        for delay_s in (10.0, 20.0):

            @callback
            def _ack_stop_check(_now: datetime) -> None:
                if (
                    self.auto_control_boiler
                    and not self._boiler_target_heating
                    and not self._is_boiler_physically_off()
                ):
                    _LOGGER.warning(
                        "Boiler %s did not confirm physical shutoff ACK; retrying off command",
                        self.boiler_entity,
                    )
                    self.hass.async_create_task(self._async_send_boiler_stop())

            self._boiler_ack_unsubs.append(
                async_call_later(self.hass, delay_s, _ack_stop_check)
            )

    @callback
    def _async_on_boiler_or_outdoor_change(
        self, event: Event[EventStateChangedData]
    ) -> None:
        """Refresh sensors on boiler ACK updates and reconcile when outdoor temperature changes."""
        self._notify_demand_listeners()
        if (
            self.outdoor_temp_entity
            and event.data["entity_id"] == self.outdoor_temp_entity
        ):
            self.hass.async_create_task(self.async_reconcile_boiler())

    @callback
    def _async_on_boiler_watchdog(self, _now: datetime) -> None:
        """Periodic 60s watchdog ensuring physical Zigbee boiler relay matches target state."""
        if not self.auto_control_boiler:
            return
        if (
            self._boiler_target_heating
            and not self._is_boiler_physically_heating()
        ):
            _LOGGER.warning(
                "Watchdog: boiler %s expected heating but physical relay is idle/off; re-sending start",
                self.boiler_entity,
            )
            self.hass.async_create_task(self._async_send_boiler_start())
        elif (
            not self._boiler_target_heating
            and not self._is_boiler_physically_off()
        ):
            _LOGGER.warning(
                "Watchdog: boiler %s expected off but physical relay is still active; re-sending stop",
                self.boiler_entity,
            )
            self.hass.async_create_task(self._async_send_boiler_stop())
