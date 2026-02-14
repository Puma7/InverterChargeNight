"""Calculation logic for SOC determination."""

import logging

_LOGGER = logging.getLogger(__name__)


def calculate_required_soc(
    forecast_energy: float,
    battery_capacity: float,
    error_margin: float,
    user_min_soc: float,
    user_max_soc: float,
) -> float | None:
    """
    Calculate the required state of charge based on PV forecast.
    
    Formula: (battery_capacity - forecast_energy) / battery_capacity * 100
    This ensures we have enough space in the battery to store the forecasted PV production.
    
    Args:
        forecast_energy: Expected PV production in kWh
        battery_capacity: Battery capacity in kWh (must be > 0)
        error_margin: Error margin as percentage (e.g., 10.0 for 10%)
        user_min_soc: Minimum allowed SOC percentage (0-100)
        user_max_soc: Maximum allowed SOC percentage (0-100, must be > user_min_soc)
    
    Returns:
        Calculated SOC percentage, or None if calculation fails
    
    Raises:
        ValueError: If input validation fails
    """
    # Input validation
    try:
        battery_capacity = float(battery_capacity)
        forecast_energy = float(forecast_energy)
        error_margin = float(error_margin)
        user_min_soc = float(user_min_soc)
        user_max_soc = float(user_max_soc)
    except (ValueError, TypeError):
        _LOGGER.error("All numeric parameters must be numbers")
        return None

    if battery_capacity <= 0:
        _LOGGER.error("Battery capacity must be a positive number")
        return None
        
    if not 0 <= user_min_soc < user_max_soc <= 100:
        _LOGGER.error(
            "Invalid SOC range: min_soc (%.1f) must be less than max_soc (%.1f) and both between 0-100",
            user_min_soc,
            user_max_soc,
        )
        return None
        
    if error_margin < 0 or error_margin > 100:
        _LOGGER.error("Error margin must be between 0 and 100")
        return None
    
    # Ensure forecast_energy is non-negative
    forecast_energy = max(0.0, float(forecast_energy))
    
    # Apply error margin to forecast (add buffer for uncertainty)
    forecast_with_margin = forecast_energy * (1 + error_margin / 100.0)
    
    # Calculate required SOC: (capacity - forecast) / capacity * 100
    # This gives us the SOC we need to have enough space for the forecast
    remaining_capacity = battery_capacity - forecast_with_margin
    
    if remaining_capacity < 0:
        # If forecast exceeds capacity, we need to be at minimum SOC
        calculated_soc = user_min_soc
        _LOGGER.warning(
            "Forecast (%.2f kWh) exceeds battery capacity (%.2f kWh), "
            "using minimum SOC: %.1f%%",
            forecast_with_margin,
            battery_capacity,
            user_min_soc,
        )
    else:
        # Calculate SOC percentage: what SOC we need to have enough space
        calculated_soc = (remaining_capacity / battery_capacity) * 100.0
    
    # Clamp to user-defined limits
    calculated_soc = max(user_min_soc, min(calculated_soc, user_max_soc))
    
    _LOGGER.debug(
        "SOC calculation: forecast=%.2f kWh (with margin: %.2f kWh), "
        "capacity=%.2f kWh, remaining=%.2f kWh, calculated=%.2f%%",
        forecast_energy,
        forecast_with_margin,
        battery_capacity,
        remaining_capacity,
        calculated_soc,
    )
    
    return round(calculated_soc, 1)

