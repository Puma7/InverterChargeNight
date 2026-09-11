# Inverter Charge Night - Home Assistant Custom Integration

A Home Assistant custom integration that intelligently calculates and sets the optimal battery state of charge (SOC) for overnight grid charging based on PV forecast data. The integration automatically controls your Kostal inverter during a configurable time window to ensure you have enough battery capacity to store the next day's solar production.

## Latest Release

- Current version: `1.0.3`
- Changelog: `CHANGELOG.md`
- Focus: startup trigger-noise reduction, robust forecast unit parsing, and audit-driven reliability fixes

## Features

- **Automatic SOC Calculation**: Calculates target SOC based on PV forecast using the formula: `(Battery Capacity - Forecast Energy) / Battery Capacity × 100`
- **Time-Based Control**: Automatically activates during a configurable time window (e.g., midnight to 6 AM)
- **Kostal Integration**: Directly controls Kostal inverter min SOC and grid charging switch
- **Forecast-Based**: Uses Solcast PV forecast data to predict next day's solar production
- **Smart Charging**: Automatically stops charging when target SOC is reached
- **Automatic Reset**: Restores original min SOC at the end of the time window
- **Highly Configurable**: Adjustable update interval and command delay to suit different inverter models and network conditions

## Requirements

- Home Assistant 2024.x or later
- Kostal inverter integration installed and configured
- Solcast integration installed and configured (or another PV forecast source)
- Battery SOC sensor available in Home Assistant

## Quality Scale (Bronze)

This custom integration aligns with the Home Assistant Bronze quality scale.
Evidence:
- UI setup via `config_flow`
- Setup/unload tests (`tests/test_setup.py`)
- Core logic tests (`tests/test_calculation.py`, `tests/test_auto_efficiency.py`)
- High-level documentation and configuration steps (this README + `documentation.md`)
- Quality scale declaration: `custom_components/inverter_charge_night/quality_scale.yaml`

## Troubleshooting

**Integration does not start or stops immediately**
- Check that all required entities exist and are available.
- Verify the time window and optional date range settings.

**Battery SOC entity is unavailable**
- The integration will skip grid charging for safety until the SOC entity is available again.

**Auto Efficient Charge Finder does not run**
- Ensure the AC limit entity and both power sensors are configured.
- Each test requires at least 30 minutes of continuous charging to be recorded.

**Download diagnostics**
- Go to **Settings → Devices & Services → Inverter Charge Night → Download diagnostics**.

## Quality Scale Checklist (Silver Readiness)

- Diagnostics supported (`diagnostics.py` + `manifest.json`)
- Troubleshooting section present (this README)
- Configurable via UI (`config_flow`)
- Tests cover setup/unload + core logic (`tests/`)

## Installation

### Method 1: Manual Installation (Recommended)

1. **Navigate to your Home Assistant configuration directory**
   - If using Home Assistant OS/Supervised: `/config/custom_components/`
   - If using Home Assistant Core: `~/.homeassistant/custom_components/`

2. **Create the integration directory**
   ```bash
   mkdir -p custom_components/inverter_charge_night
   ```

3. **Copy all files** from this repository into the `custom_components/inverter_charge_night/` directory:
   ```
   custom_components/inverter_charge_night/
   ├── __init__.py
   ├── manifest.json
   ├── config_flow.py
   ├── const.py
   ├── calculation.py
   ├── sensor.py
   ├── switch.py
   ├── number.py
   ├── binary_sensor.py
   └── strings.json
   ```

4. **Restart Home Assistant**
   - Go to **Settings** → **System** → **Hardware**
   - Click the three dots menu (⋮) → **Restart Home Assistant**

### Method 2: Using HACS (Home Assistant Community Store)

1. **Install HACS** if you haven't already (see [HACS documentation](https://hacs.xyz/docs/setup/download))

2. **Add this repository to HACS**:
   - Go to **HACS** → **Integrations**
   - Click the three dots menu (⋮) → **Custom repositories**
   - Add repository URL: `https://github.com/Puma7/InverterChargeNight`
   - Category: **Integration**
   - Click **Add**

3. **Install the integration**:
   - Search for "Inverter Charge Night" in HACS
   - Click **Download**
   - Restart Home Assistant

## Configuration

### Step 1: Find Your Entity IDs

Before configuring, you need to identify the following entities in Home Assistant:

1. **Kostal Min SOC Number Entity**: 
   - Look for a `number` entity from your Kostal integration (e.g., `number.kostal_plenticore_min_soc`)
   - This entity controls the minimum state of charge

2. **Kostal Grid Charge Switch**:
   - Look for a `switch` entity that enables/disables grid charging (e.g., `switch.kostal_plenticore_grid_charge`)
   - This switch controls whether the inverter charges from the grid

3. **PV Forecast Entity**:
   - Look for your Solcast forecast entity (e.g., `sensor.solcast_forecast_today`)
   - This should provide forecast data in kWh

4. **Battery SOC Sensor**:
   - Look for your battery state of charge sensor (e.g., `sensor.battery_soc`)
   - This should show current battery SOC as a percentage (0-100)

To find entities:
- Go to **Settings** → **Devices & Services** → **Entities**
- Use the search/filter to find entities by name or domain

### Step 2: Configure the Integration

1. **Add the integration**:
   - Go to **Settings** → **Devices & Services**
   - Click **Add Integration** (bottom right)
   - Search for "Inverter Charge Night"
   - Click on it

2. **Fill in the configuration form**:

   **Basic Settings:**
   - **Integration Name**: Give your integration a name (e.g., "Inverter Charge Night")

   **Kostal Entities:**
   - **Kostal Min SOC Number Entity**: Select the number entity that controls min SOC
   - **Kostal Grid Charge Switch**: Select the switch that controls grid charging

   **Data Sources:**
   - **PV Forecast Entity (Solcast)**: Select your Solcast forecast entity
   - **Battery SOC Sensor**: Select your battery SOC sensor

   **Battery Configuration:**
   - **Battery Capacity (kWh)**: Enter your total battery capacity (e.g., 35.8)

   **Time Window:**
   - **Start Time (HH:MM)**: When night charging should start (e.g., `00:00` for midnight)
   - **End Time (HH:MM)**: When night charging should end (e.g., `05:59` for 5:59 AM)

   **SOC Limits:**
   - **Minimum SOC (%)**: Minimum allowed SOC (e.g., `8.0`)
   - **Maximum SOC (%)**: Maximum allowed SOC (e.g., `100.0`)

   **Forecast Settings:**
   - **Forecast Error Margin (%)**: Safety buffer for forecast uncertainty (e.g., `10.0` for 10% buffer)

   **Advanced Settings:**
   - **Update Interval (seconds)**: How often to refresh data (default: `900` / 15 minutes)
   - **Command Delay (seconds)**: Delay between setting min SOC and enabling grid charge (default: `0.1`)

   **Reset Value:**
   - **Default Min SOC to restore (%)**: The min SOC value to restore at end time (typically `8.0` for Kostal)

3. **Submit the configuration**
   - Review all settings
   - Click **Submit**

### Step 3: Verify Installation

After configuration, you should see new entities:

- **`sensor.inverter_charge_night_calculated_soc`** - Shows the calculated target SOC
  - Unit: `%`
  - Attributes: `is_active`, `target_reached`, `current_soc`
  
- **`switch.inverter_charge_night_enabled`** - Enable/disable the integration
  - When disabled: Resets all settings and stops controlling inverter
  
- **`binary_sensor.inverter_charge_night_active`** - Shows if currently in active window
  - `on`: Currently in configured time window
  - `off`: Outside time window
  
- **`number.inverter_charge_night_min_soc_override`** - Manual override for target SOC
  - Range: 0 - 100
  - Step: 1
  - When set: Overrides automatic calculation while active

- **`number.inverter_charge_night_snow_nights`** - Snow on the modules: charge the next N nights to the maximum
  - Range: 0 - 14, Step: 1
  - Bei Schnee auf den Modulen: Anzahl der nächsten Nächte, in denen bis zum Maximum geladen wird; zählt automatisch herunter.
  - Overrides the forecast and the manual override; counts down at every window end

- **`switch.inverter_charge_night_auto_efficient_charge_finder`** - Auto efficient charge finder
  - Finds the most efficient AC charge limit and turns itself off when done

- **`sensor.inverter_charge_night_best_charge_power`** - Best AC charge power found
  - Unit: `W`
  - Shows the last stored optimal point for AC charging efficiency

## Example Automations

**Enable nightly charging**
```yaml
alias: Enable Inverter Charge Night
trigger:
  - platform: time
    at: "00:00:00"
action:
  - service: switch.turn_on
    target:
      entity_id: switch.inverter_charge_night_enabled
```

**Disable in backup mode**
```yaml
alias: Disable Inverter Charge Night on Backup
trigger:
  - platform: state
    entity_id: binary_sensor.inverter_backup_mode
    to: "on"
action:
  - service: switch.turn_off
    target:
      entity_id: switch.inverter_charge_night_enabled
```

**Run Auto Efficient Charge Finder**
```yaml
alias: Run Auto Efficient Charge Finder
trigger:
  - platform: time
    at: "01:00:00"
action:
  - service: switch.turn_on
    target:
      entity_id: switch.inverter_charge_night_auto_efficient_charge_finder
```

## Use Cases

- **Winter / low PV forecast**: Use higher target SOC to ensure enough overnight capacity.
- **Summer / high PV forecast**: Use lower target SOC to create more daytime storage headroom.
- **Backup/Island mode**: Disable integration when backup mode is active to avoid AC charging.
- **Efficiency tuning**: Enable Auto Efficient Charge Finder for a few nights to find best AC limit.

## Dashboard Examples (Lovelace)

**Simple status card**
```yaml
type: entities
title: Inverter Charge Night
entities:
  - switch.inverter_charge_night_enabled
  - binary_sensor.inverter_charge_night_active
  - sensor.inverter_charge_night_calculated_soc
  - number.inverter_charge_night_min_soc_override
  - number.inverter_charge_night_snow_nights
```

**Auto Finder card**
```yaml
type: entities
title: Auto Efficient Charge
entities:
  - switch.inverter_charge_night_auto_efficient_charge_finder
  - sensor.inverter_charge_night_best_charge_power
```

## Entity List (End-User)

- **`switch.inverter_charge_night_enabled`**  
  Turns the integration on/off. Disabling resets grid charging settings.

- **`binary_sensor.inverter_charge_night_active`**  
  Indicates whether the current time is inside the configured window.

- **`sensor.inverter_charge_night_calculated_soc`**  
  Target SOC based on PV forecast. Attributes show `current_soc`, `target_reached`.

- **`number.inverter_charge_night_min_soc_override`**  
  Manual override for target SOC (integer %). Overrides automatic calculation.

- **`number.inverter_charge_night_snow_nights`**  
  Bei Schnee auf den Modulen: Anzahl der nächsten Nächte, in denen bis zum Maximum geladen wird; zählt automatisch herunter.

- **`switch.inverter_charge_night_auto_efficient_charge_finder`**  
  Starts the efficiency search for best AC charge limit, auto-disables when done.

- **`sensor.inverter_charge_night_best_charge_power`**  
  Best AC charge power found (W). Used as target for AC limit.

## How It Works

### Calculation Formula

The integration calculates the target SOC using:

```
Target SOC = (Battery Capacity - Forecast Energy × (1 + Error Margin)) / Battery Capacity × 100
```

**Step-by-step:**
1. Apply error margin to forecast: `Forecast × (1 + Error Margin / 100)`
2. Calculate remaining capacity: `Battery Capacity - Adjusted Forecast`
3. Convert to percentage: `(Remaining Capacity / Battery Capacity) × 100`
4. Clamp to user-defined limits: `max(user_min_soc, min(calculated_soc, user_max_soc))`

**Example:**
- Battery Capacity: 35.8 kWh
- Forecast Energy: 5 kWh
- Error Margin: 10%
- Adjusted Forecast: 5 × 1.10 = 5.5 kWh
- Remaining Capacity: 35.8 - 5.5 = 30.3 kWh
- Calculation: (30.3 / 35.8) × 100 = 84.6%
- Final (if min=8%, max=100%): 84.6%

**Edge Cases:**
- If forecast exceeds battery capacity: Uses minimum SOC
- If forecast is 0: Uses maximum SOC (or user_max_soc)
- Result is always clamped between user_min_soc and user_max_soc

### Operation Flow

**During Active Window (e.g., 00:00 - 05:59):**

1. Integration reads PV forecast from Solcast (supports multiple data formats)
2. Calculates target SOC based on forecast and battery capacity
3. Validates target SOC is within user-defined limits
4. Stores original Kostal min SOC value (if not already stored)
5. Sets Kostal min SOC to calculated value (only if different from current)
6. Enables Kostal grid charging switch (only if currently off)
7. Monitors battery SOC every 15 minutes
8. When target SOC is reached, turns off grid charging (min SOC remains set)
9. Recalculates periodically (every 15 minutes) if forecast updates
10. Rate limiting prevents excessive service calls

**At Window End (e.g., 05:59):**

1. Resets Kostal min SOC to original value (or configured default if original not available)
2. Turns off Kostal grid charging switch
3. Clears stored original min SOC value
4. Resets state flags (is_active, target_reached)
5. Integration enters standby mode until next window

**Safety Mechanisms:**
- Automatic reset if integration is unloaded during active window
- State flags always reset even if reset operation fails
- Original min SOC value stored and restored
- Fallback to configured default if original value unavailable
- Comprehensive error handling with logging

## Configuration Examples

### Example 1: Standard Setup

- **Battery Capacity**: 35.8 kWh
- **Start Time**: 00:00
- **End Time**: 05:59
- **Min SOC**: 8%
- **Max SOC**: 100%
- **Error Margin**: 10%
- **Default Min SOC**: 8%

### Example 2: Conservative Setup

- **Battery Capacity**: 20.0 kWh
- **Start Time**: 22:00
- **End Time**: 06:00
- **Min SOC**: 10%
- **Max SOC**: 90%
- **Error Margin**: 15%
- **Default Min SOC**: 10%

## Troubleshooting

### Integration Not Appearing

- **Check file structure**: Ensure all files are in `custom_components/inverter_charge_night/`
- **Check manifest.json**: Verify it's valid JSON
- **Restart Home Assistant**: A restart is required after installation
- **Check logs**: Look for errors in **Settings** → **System** → **Logs**

### Entities Not Found

- **Verify entity IDs**: Use **Settings** → **Devices & Services** → **Entities** to find correct entity IDs
- **Check entity domains**: 
  - Kostal min SOC should be a `number` entity
  - Grid charge should be a `switch` entity
  - Battery SOC should be a `sensor` entity

### Calculation Not Working

- **Check forecast entity**: Verify the Solcast entity is providing data in kWh
- **Check battery capacity**: Ensure it's entered correctly in kWh (not Wh)
- **Check logs**: Look for calculation errors in the logs

### Charging Not Starting

- **Check time window**: Verify current time is within the configured window
- **Check switch state**: Ensure the integration switch is enabled
- **Check Kostal entities**: Verify Kostal entities are accessible and not in unavailable state
- **Check logs**: Look for control errors in the logs

### SOC Not Resetting

- **Check end time**: Verify the end time is configured correctly
- **Check logs**: Look for reset errors at end time
- **Manual reset**: You can manually set the Kostal min SOC if needed

## Advanced Usage

### Manual Override

You can manually override the calculated SOC using the `number.inverter_charge_night_min_soc_override` entity. This will override the automatic calculation while the integration is active. The override value is applied immediately if the integration is in the active window.

### Disabling the Integration

Use the `switch.inverter_charge_night_enabled` entity to temporarily disable the integration. When disabled:
- All Kostal settings are reset to original values
- Integration stops controlling the inverter
- State flags are reset
- Integration remains configured but inactive

### Monitoring

Monitor the integration status using:
- **`sensor.inverter_charge_night_calculated_soc`** - Current calculated target SOC
  - Check attributes for `is_active`, `target_reached`, `current_soc`
- **`binary_sensor.inverter_charge_night_active`** - Whether currently in active window
- **Home Assistant Logs** - Detailed operation logs at INFO/DEBUG level

### Update Interval

The integration updates every **15 minutes** (900 seconds) by default. This is defined in `const.py` as `DEFAULT_UPDATE_INTERVAL`. During each update:
- Forecast data is read
- SOC is recalculated
- Kostal entities are controlled if needed
- Battery SOC is checked for target reached condition

## Support

For issues, questions, or contributions:

- **GitHub Issues**: [Create an issue](https://github.com/Puma7/InverterChargeNight/issues)
- **Home Assistant Community**: [Forum Discussion](https://community.home-assistant.io/)

## License

This integration is provided as-is for personal use.

## Technical Details

### Architecture

The integration uses Home Assistant's standard patterns:

- **Coordinator Pattern**: `DataUpdateCoordinator` manages state and updates
- **Platform Entities**: Separate entities for sensor, switch, binary_sensor, and number
- **Config Flow**: UI-based configuration with entity selectors
- **Time-based Triggers**: `async_track_time_change` for window start/end

### File Structure

```
custom_components/inverter_charge_night/
├── __init__.py              # Main integration setup and coordinator
├── manifest.json            # Integration metadata
├── config_flow.py           # Configuration UI
├── const.py                 # Constants and configuration keys
├── calculation.py           # SOC calculation logic
├── sensor.py                # Calculated SOC sensor entity
├── switch.py                # Enable/disable switch entity
├── number.py                # Manual override number entity
├── binary_sensor.py         # Active window binary sensor
└── strings.json             # UI strings and translations
```

### Configuration Keys

All configuration options stored in `entry.data`:
- `kostal_min_soc_entity` - Kostal min SOC number entity ID
- `kostal_grid_charge_switch` - Kostal grid charge switch entity ID
- `pv_forecast_entity` - PV forecast entity ID (Solcast)
- `battery_soc_entity` - Battery SOC sensor entity ID
- `battery_capacity` - Battery capacity in kWh (float)
- `start_time` - Window start time (HH:MM format)
- `end_time` - Window end time (HH:MM format)
- `user_min_soc` - Minimum allowed SOC (0-100)
- `user_max_soc` - Maximum allowed SOC (0-100, must be > user_min_soc)
- `forecast_error_margin` - Forecast error margin percentage (0-100)
- `default_min_soc` - Default min SOC to restore at end time (0-100)

### Default Values

Defined in `const.py`:
- `DEFAULT_MIN_SOC = 8.0`
- `DEFAULT_MAX_SOC = 100.0`
- `DEFAULT_START_TIME = "00:00"`
- `DEFAULT_END_TIME = "05:59"`
- `DEFAULT_FORECAST_ERROR_MARGIN = 10.0`
- `DEFAULT_UPDATE_INTERVAL = 900` (15 minutes)

### Safety Features

The integration includes multiple safety mechanisms:

1. **State Persistence**: Original min SOC stored and restored
2. **Error Recovery**: Graceful handling of entity unavailability
3. **Validation**: Target SOC validated before applying
4. **Rate Limiting**: Prevents excessive service calls
5. **Unload Safety**: Automatic reset if integration unloaded during active window
6. **Fallback Mechanisms**: Uses configured defaults if original values unavailable
7. **Comprehensive Logging**: All operations logged at appropriate levels

### Forecast Data Parsing

The integration supports multiple Solcast data formats:
- Direct state value (kWh or Wh - auto-detected)
- `forecast` attribute (array of forecast objects)
- `today_forecast` attribute
- `forecast_today` attribute

If forecast data is unavailable, integration uses 0 kWh (resulting in maximum SOC).

### Error Handling

All critical operations include error handling:
- Service calls wrapped in try-except blocks
- Entity state checks before operations
- Graceful degradation when entities unavailable
- State flags reset even if operations fail
- Comprehensive error logging with full exception info

### Developer Checks (CI)

Recommended checks for CI or local validation:
- `pytest -q -p pytest_asyncio.plugin -p pytest_cov.plugin`
- `pyright`
- `mypy custom_components/inverter_charge_night`

## Changelog

### Version 1.0.0
- Initial release
- SOC calculation based on PV forecast with error margin
- Kostal inverter control (min SOC and grid charge switch)
- Time-based activation with configurable window
- Automatic reset at end time with original value restoration
- Manual override capability
- Comprehensive error handling and safety mechanisms
- Rate limiting to prevent excessive service calls
- Support for multiple Solcast forecast data formats

