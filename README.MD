# Bus Charging Scheduler

A Streamlit app that schedules electric bus charging along the Bengaluru → Kochi route. Given a scenario (departure times, operators, route config, and weights), the scheduler decides which stations each bus charges at, the order buses use each charger, and produces a full timeline for every bus.

---

## Quick Start

### Prerequisites
- Python 3.10+
- Git

### Install and run locally

```bash
# Clone the repo
git clone https://github.com/hirim10/bus_charging_scheduler.git
cd bus_charging_scheduler

# Install dependencies
pip install -r requirements.txt

# Run the app
python -m streamlit run app.py
```

The app opens at `http://localhost:8501`. Pick a scenario from the dropdown and the scheduler runs instantly.

---

## Project Structure

```
bus_charging_scheduler/
├── app.py                  # Streamlit UI — scenario picker, timetable, station view
├── scheduler.py            # Core scheduling engine + data classes
├── scenario_loader.py      # JSON loading, validation, time utilities
├── requirements.txt        # pip dependencies
├── README.md
├── ARCHITECTURE.md
└── scenarios/
    ├── scenario_1.json     # Even spacing (baseline)
    ├── scenario_2.json     # Bunched start (heavy early contention)
    ├── scenario_3.json     # Asymmetric load (lopsided directions)
    ├── scenario_4.json     # Operator heavy — KPN dominance
    └── scenario_5.json     # Worst case convergence (maximum contention)
```

---

## How to Change a Weight

Every scenario's weights live in one place — the scenario JSON file under the `weights` key:

```json
"weights": {
    "individual": 1.0,
    "operator": 2.0,
    "overall": 1.0
}
```

Change the value and rerun the app. No code changes needed.

- `individual` — how much to penalise long waits for a single bus
- `operator` — how much to penalise imbalance across an operator's fleet
- `overall` — how much to penalise total network delay

Higher weight = that concern has more influence on charger slot decisions.

---

## How to Add a New Scenario

1. Copy any existing scenario JSON from `scenarios/`
2. Edit the fields — buses, departure times, weights, physics, stations
3. Save it as `scenarios/scenario_6.json` (or any `scenario_*.json` name)
4. It appears automatically in the dropdown on next run — no code changes needed

---

## How to Add a New Soft Rule

Soft rules are the criteria the scheduler uses to pick charger slots. Adding one is a 2-step process and touches nothing else in the engine.

**Step 1 — Define the rule method in `scheduler.py`:**

```python
def _rule_priority_operator(
    self,
    bus: Bus,
    station_id: str,
    arrival_time: float,
    charger_free_time: float
) -> float:
    """
    Example new rule: give priority buses a lower cost score
    so they are always preferred over non-priority buses.
    """
    if bus.operator == "kpn":
        return -50.0 * self.weights.individual  # negative = preferred
    return 0.0
```

**Step 2 — Register it in `__init__`:**

```python
self.soft_rules = [
    self._rule_individual_wait,
    self._rule_operator_fairness,
    self._rule_overall_time,
    self._rule_priority_operator,   # <-- add here
]
```

That's it. The engine calls every rule in the list automatically via `_score_candidate`. Nothing else changes.

---

## How to Add a New Hard Rule

Hard rules (constraints that must never be violated) go in `_valid_charging_stops` inside `scheduler.py`. That method decides which stations a bus is forced to stop at. Add your constraint logic there — for example, forcing a stop at a specific station for a maintenance check.

---

## How to Add a New Station

In the scenario JSON, add an entry to `stations` and add the corresponding segment to `route.segments`:

```json
"stations": [
    {"id": "A", "chargers": 1},
    {"id": "A2", "chargers": 2},
    {"id": "B", "chargers": 1},
    ...
],
"route": {
    "stops": ["Bengaluru", "A", "A2", "B", "C", "D", "Kochi"],
    "segments": [
        {"from": "Bengaluru", "to": "A", "distance_km": 100},
        {"from": "A", "to": "A2", "distance_km": 60},
        {"from": "A2", "to": "B", "distance_km": 60},
        ...
    ]
}
```

No code changes needed.

---

## How to Add More Chargers at a Station

Change the `chargers` value in the scenario JSON:

```json
{"id": "B", "chargers": 2}
```

The scheduler automatically manages multiple charger slots at that station. No code changes needed.

---

## Assumptions

- All buses start with a full charge (240 km range) from their origin
- Charging always fills to full and always takes exactly 25 minutes
- Buses travel at a constant speed (no traffic, no variation within a scenario)
- The greedy charging strategy delays charging as long as safely possible — this minimises stops and keeps buses moving
- Buses are processed in departure order — earlier departures get first access to chargers
- When multiple charger slots exist at a station, the scheduler scores each slot and picks the lowest-cost one

---

## Requirements

```
streamlit
pandas
numpy
```