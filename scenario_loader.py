import json
from pathlib import Path
from typing import Dict, Any, List


def load_scenario(path: str) -> Dict[str, Any]:
    """Load and validate a scenario JSON file."""
    with open(path) as f:
        data = json.load(f)
    _validate_scenario(data)
    return data


def load_all_scenarios(directory: str = "scenarios") -> Dict[str, tuple]:
    """
    Load all scenario_*.json files from a directory.
    Returns dict of {scenario_name: (file_path, scenario_data)}
    """
    scenario_dir = Path(directory)
    scenario_files = sorted(scenario_dir.glob("scenario_*.json"))

    scenarios = {}
    for f in scenario_files:
        data = load_scenario(str(f))
        scenarios[data["name"]] = (str(f), data)

    return scenarios


def parse_time(t: str) -> float:
    """Convert HH:MM string to minutes from midnight."""
    h, m = map(int, t.split(":"))
    return h * 60 + m


def fmt_time(minutes: float) -> str:
    """Convert minutes from midnight to HH:MM string."""
    h = int(minutes) // 60
    m = int(minutes) % 60
    return f"{h:02d}:{m:02d}"


def _validate_scenario(data: Dict[str, Any]) -> None:
    """
    Basic validation — catches malformed scenario files early
    with a clear error message rather than a cryptic crash.
    """
    required_top_level = ["scenario_id", "name", "route", "stations",
                           "physics", "weights", "buses"]
    for key in required_top_level:
        if key not in data:
            raise ValueError(f"Scenario missing required field: '{key}'")

    required_physics = ["battery_range_km", "charge_time_min", "speed_kmh"]
    for key in required_physics:
        if key not in data["physics"]:
            raise ValueError(f"Physics block missing field: '{key}'")

    required_weights = ["individual", "operator", "overall"]
    for key in required_weights:
        if key not in data["weights"]:
            raise ValueError(f"Weights block missing field: '{key}'")

    if "stops" not in data["route"] or "segments" not in data["route"]:
        raise ValueError("Route must have 'stops' and 'segments'")

    if len(data["buses"]) == 0:
        raise ValueError("Scenario must have at least one bus")

    # Validate each bus has required fields
    for bus in data["buses"]:
        for field in ["id", "operator", "direction", "departure"]:
            if field not in bus:
                raise ValueError(f"Bus {bus.get('id', '?')} missing field: '{field}'")