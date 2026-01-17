"""Test calculation logic."""
from custom_components.inverter_charge_night.calculation import calculate_required_soc


def test_calculate_required_soc_basic():
    result = calculate_required_soc(
        forecast_energy=2.0,
        battery_capacity=10.0,
        error_margin=0.0,
        user_min_soc=8.0,
        user_max_soc=100.0,
    )
    assert result == 80.0


def test_calculate_required_soc_forecast_exceeds_capacity():
    result = calculate_required_soc(
        forecast_energy=50.0,
        battery_capacity=10.0,
        error_margin=0.0,
        user_min_soc=8.0,
        user_max_soc=100.0,
    )
    assert result == 8.0


def test_calculate_required_soc_invalid_capacity():
    assert (
        calculate_required_soc(
            forecast_energy=2.0,
            battery_capacity=0,
            error_margin=0.0,
            user_min_soc=8.0,
            user_max_soc=100.0,
        )
        is None
    )


def test_calculate_required_soc_invalid_forecast():
    assert (
        calculate_required_soc(
            forecast_energy="bad",
            battery_capacity=10.0,
            error_margin=0.0,
            user_min_soc=8.0,
            user_max_soc=100.0,
        )
        is None
    )


def test_calculate_required_soc_invalid_soc_range():
    assert (
        calculate_required_soc(
            forecast_energy=2.0,
            battery_capacity=10.0,
            error_margin=0.0,
            user_min_soc=50.0,
            user_max_soc=40.0,
        )
        is None
    )


def test_calculate_required_soc_invalid_error_margin():
    assert (
        calculate_required_soc(
            forecast_energy=2.0,
            battery_capacity=10.0,
            error_margin=101.0,
            user_min_soc=8.0,
            user_max_soc=100.0,
        )
        is None
    )


def test_calculate_required_soc_non_numeric_params():
    assert (
        calculate_required_soc(
            forecast_energy=2.0,
            battery_capacity=10.0,
            error_margin="bad",
            user_min_soc=8.0,
            user_max_soc=100.0,
        )
        is None
    )


def test_calculate_required_soc_negative_forecast_clamped():
    result = calculate_required_soc(
        forecast_energy=-5.0,
        battery_capacity=10.0,
        error_margin=0.0,
        user_min_soc=0.0,
        user_max_soc=100.0,
    )
    assert result == 100.0


def test_calculate_required_soc_clamps_to_max():
    result = calculate_required_soc(
        forecast_energy=0.0,
        battery_capacity=10.0,
        error_margin=0.0,
        user_min_soc=8.0,
        user_max_soc=50.0,
    )
    assert result == 50.0
