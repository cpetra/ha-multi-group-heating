# Multi-Group TRV Schedule & Boiler Demand Controller (`multi_group_heating`)

A standalone **Home Assistant Custom Integration** (for the Home Assistant Marketplace / HACS) that implements a complete **3-tier smart heating controller**:
1. **Tier 1 — Schedule & Mode Manager**: Native `select` (`Away`, `Comfort`, `Day`, `Night`), 4 configurable daily `time` transition entities (`Morning Comfort`, `Day`, `Evening Comfort`, `Night`), and 20 `number` preset entities across **5 heating groups**.
2. **Tier 2 — Virtual Group Target Binding & Sleepy-Zigbee Retry**: 5 virtual group target `number` entities (`TS Target <Group>`) that broadcast setpoints to each group's TRVs with a configurable `10s` verification retry while preserving manual single-room overrides between transitions.
3. **Tier 3 — Anti-Short-Cycling Boiler Hysteresis Controller (`0.5 °C` Deadband)**: Native `sensor` (`start` / `keep` / `stop`) that drives the central gas boiler thermostat (`climate`) only when a room cools by at least $\Delta T$ below its target and keeps heating until all active rooms reach target.

---

## Hardware Note: Tuya / Moes Zigbee TRVs (`_TZE204_ltwbm23f` / `TS0601`)

> [!IMPORTANT]
> If you use Tuya/Moes `TS0601` (`_TZE204_ltwbm23f`) Zigbee TRVs in ZHA or Zigbee2MQTT, set each valve's **`Hysteresis mode` (`select.*_hysteresis_mode`) to `Eco`**, not `Comfort`:
> - **`Comfort`** uses a sluggish proportional PID curve that opens the valve only **`1%–7%`** when a room cools $0.5\text{–}1.0\text{ °C}$ below target, starving the radiator while the boiler burns gas.
> - **`Eco`** uses a direct **`0.5 °C` hysteresis switch** that opens the valve **`100%`** as soon as $T_{\text{current}} \le T_{\text{target}} - 0.5\text{ °C}$, matching the boiler demand threshold and heating rooms in ~10 minutes.

---

## Installation via Home Assistant Marketplace (HACS)

1. Open **Marketplace (HACS)** in Home Assistant → **3-dots menu (top right) → Custom repositories**.
2. Add your GitHub repository URL with Category: **Integration**.
3. Download **Multi-Group Boiler Heating** and restart Home Assistant.
4. Go to **Settings → Devices & Services → Add Integration → Multi-Group Boiler Heating** and select:
   - Your **Boiler Thermostat Entity** (`climate.*`)
   - **Lower Hysteresis Deadband** (default `0.5 °C`)
   - **Boiler Firing Setpoint** (default `25.0 °C`)
   - **Sleepy Zigbee Verification Retry Delay** (default `10 s`)
   - Up to **5 Heating Groups** (Name + assigned TRV `climate.*` entities; groups with no TRVs yet are still created and ready for future pairing)

---

## Entities Created Automatically

 All entities are grouped under a single **Multi-Group Heating** service device and restore their state across Home Assistant restarts (`RestoreEntity`):

| Platform | Entities Created | Purpose |
|---|---|---|
| `select` | `select.*_heating_mode` | Active schedule mode (`Away`, `Comfort`, `Day`, `Night`) |
| `time` (×4) | `time.*_morning_comfort_start`, `day_mode_start`, `evening_comfort_start`, `night_mode_start` | Daily transition times (bypassed automatically while `Away` is active) |
| `number` (×5) | `number.*_ts_target_<group>` | Virtual group target temperature (`5.0–30.0 °C`) |
| `number` (×20) | `number.*_<mode>_temp_<group>` | Per-group preset temperatures for `Comfort`, `Day`, `Night`, `Away` |
| `sensor` | `sensor.*_heating_demand_action` | Aggregate boiler demand (`start`, `keep`, `stop`) + diagnostic attributes (`active_trvs`, `need_heat_trvs`, `satisfied_trvs`) |

---

## Repository Structure

```text
ha-multi-group-heating/
├── .github/
│   └── workflows/
│       └── validate.yaml                     # GitHub Actions: hassfest + HACS validation
├── custom_components/
│   └── multi_group_heating/
│       ├── __init__.py                       # Integration setup & unload
│       ├── config_flow.py                    # UI Config Flow & Options Flow (5 groups + boiler)
│       ├── const.py                          # Domain constants & defaults
│       ├── coordinator.py                    # 3-Tier controller (schedule, Zigbee retry, hysteresis)
│       ├── manifest.json                     # Home Assistant integration manifest
│       ├── number.py                         # 5 Virtual Group Targets + 20 Mode Preset numbers
│       ├── select.py                         # Heating Mode selector (Away, Comfort, Day, Night)
│       ├── sensor.py                         # Heating Demand Action sensor (start / keep / stop)
│       ├── strings.json                      # UI strings
│       ├── time.py                           # 4 Daily schedule transition time entities
│       └── translations/
│           └── en.json                       # English UI translations
├── .gitignore
├── hacs.json                                 # Marketplace metadata
├── LICENSE                                   # MIT License
└── README.md
```
