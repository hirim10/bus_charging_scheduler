# Architecture — Bus Charging Scheduler

---

## Scheduler Approach

The scheduler uses a **greedy simulation with a weighted cost scoring system**.

Each bus is walked through its route segment by segment in departure order. At each station it must charge at, the scheduler scores every available charger slot using a set of soft rules and picks the lowest-cost slot. The score is the sum of all soft rule outputs, each multiplied by its weight.

### Why greedy simulation and not something else

**Why not constraint satisfaction (OR-Tools, PuLP)?**
Constraint solvers are powerful but treat the entire schedule as one monolithic problem. Every time a new rule is added, the constraint model has to be restructured. They're also hard to reason about when something goes wrong — debugging a failed constraint is opaque. For a system where rules will keep changing as the team learns from the field, a solver would become a liability.

**Why not a genetic algorithm or search-based approach?**
These are good for global optimisation but overkill here. The problem has a natural ordering — buses depart at fixed times, travel at fixed speeds, and arrive at stations in a predictable sequence. There's no need to search the whole space when you can simulate forward in time and make locally good decisions at each choice point.

**Why greedy simulation with weighted scoring?**
- Adding a new rule is defining one function and appending it to a list — the engine doesn't change
- Weights are single values in the scenario file — changing one takes 5 seconds
- The simulation is fully transparent — you can trace exactly why any bus was assigned any slot
- It scales linearly with the number of buses and stations — no combinatorial explosion
- It handles multiple chargers per station, multiple operators, and any route shape without structural changes

The tradeoff is that greedy simulation doesn't guarantee a globally optimal schedule. A later bus might have been better served by a slot that an earlier bus took. This is an accepted tradeoff — the system is designed for operational transparency and extensibility, not mathematical optimality. In a real deployment, "defensible and traceable" matters more than "provably optimal."

---

## Data Structure Design

Each scenario is a self-contained JSON file. The scheduler reads everything it needs from that file — route, stations, physics, weights, and buses. Nothing is hardcoded.

```json
{
  "scenario_id": "scenario_1",
  "name": "Even Spacing",
  "description": "...",
  "route": {
    "stops": ["Bengaluru", "A", "B", "C", "D", "Kochi"],
    "segments": [
      {"from": "Bengaluru", "to": "A", "distance_km": 100},
      ...
    ]
  },
  "stations": [
    {"id": "A", "chargers": 1},
    ...
  ],
  "physics": {
    "battery_range_km": 240,
    "charge_time_min": 25,
    "speed_kmh": 60
  },
  "weights": {
    "individual": 1.0,
    "operator": 1.0,
    "overall": 1.0
  },
  "buses": [
    {"id": "bus-BK-01", "operator": "kpn", "direction": "BK", "departure": "19:00"},
    ...
  ]
}
```

### Why this structure

Every field that could reasonably change is a data field, not a code constant. The route is not hardcoded. The station list is not hardcoded. The number of chargers is not hardcoded. The speed is not hardcoded. Operators are plain strings — adding a new one requires zero code changes.

The scheduler reads `stops` and `segments` to derive all route logic. Direction is encoded as `"BK"` (Bengaluru → Kochi) or `"KB"` (Kochi → Bengaluru) — the scheduler reverses the stop and segment lists automatically for KB buses, so bidirectional routing requires no special casing.

---

## Anticipated Future Changes

These are the changes I designed for explicitly. Each can be made through data alone — no code changes required.

### 1. Add a new station mid-route
Add the stop to `route.stops` and the corresponding segment to `route.segments`. Add the station to `stations`. The scheduler derives all charging logic from these lists dynamically.

```json
"stops": ["Bengaluru", "A", "A2", "B", "C", "D", "Kochi"],
"segments": [
  {"from": "Bengaluru", "to": "A", "distance_km": 100},
  {"from": "A", "to": "A2", "distance_km": 60},
  {"from": "A2", "to": "B", "distance_km": 60},
  ...
]
```

### 2. Add more chargers at a busy station
Change the `chargers` value in the station entry. The charger slot manager in `Scheduler.__init__` initialises slots based on this value automatically.

```json
{"id": "B", "chargers": 3}
```

### 3. Add a new operator
Just use a new string in the `operator` field of any bus. No registration, no enum, no code change. The operator fairness rule groups buses by operator string dynamically.

```json
{"id": "bus-BK-11", "operator": "zingbus", "direction": "BK", "departure": "21:30"}
```

### 4. Change segment distances
Edit `distance_km` in any segment. All travel times and charging plans recalculate from this value on every run.

### 5. Change bus speed
Edit `speed_kmh` in `physics`. All travel times recalculate. This lets you model night driving, road conditions, or different vehicle classes per scenario without touching code.

### 6. Change battery range or charge time
Edit `battery_range_km` or `charge_time_min` in `physics`. The range constraint check and charging event timing both read from these values.

### 7. Tune optimisation weights
Edit the three values in `weights`. Each soft rule multiplies its raw cost by the relevant weight. Changing `operator` from `1.0` to `3.0` makes operator fairness three times more influential in charger slot selection.

### 8. Add more buses to a scenario
Add entries to the `buses` array. The scheduler processes all buses in departure order regardless of count.

### 9. Add a completely new route (e.g. Bengaluru → Mumbai)
Create a new scenario JSON with a different `stops` and `segments` list. The scheduler has no knowledge of specific city names — it works on any ordered list of stops with distances.

### 10. Support multiple routes sharing a station
Each scenario is independent today. To support shared stations across routes, promote `stations` to a shared config file and reference station IDs from multiple scenario files. The station charger state would need to be initialised once across all active scenarios. This is a code change but a small and localised one — only `scenario_loader.py` and `Scheduler.__init__` are affected.

### 11. Add time-of-day electricity pricing
Add a `pricing` block to the scenario JSON:

```json
"pricing": [
  {"from": "22:00", "to": "06:00", "cost_per_min": 0.8},
  {"from": "06:00", "to": "22:00", "cost_per_min": 1.2}
]
```

Then add a `_rule_electricity_cost` soft rule that looks up the price at `charge_start` time and returns it as a cost. Register it in `self.soft_rules`. Nothing else changes.

### 12. Add priority buses (e.g. express or medical)
Add a `priority` field to the bus entry in the JSON:

```json
{"id": "bus-BK-05", "operator": "kpn", "direction": "BK", "departure": "20:00", "priority": "high"}
```

Add a `_rule_priority` soft rule that returns a large negative cost (i.e. strong preference) for high-priority buses. Register it in `self.soft_rules`. No engine changes.

### 13. Add driver shift constraints
Add a `max_shift_hours` field to the bus or operator config. Add a hard rule in `_valid_charging_stops` or a soft rule that penalises schedules pushing a bus past its driver's shift limit.

### 14. Add per-operator SLA targets (max acceptable wait)
Add an `sla` block to the scenario:

```json
"sla": {
  "kpn": {"max_wait_min": 20},
  "freshbus": {"max_wait_min": 30}
}
```

Add a `_rule_sla_breach` soft rule that returns a large penalty when `wait_time` would exceed the operator's SLA. Register in `self.soft_rules`.

### 15. Change the number of buses per scenario
The scheduler has no hardcoded bus count. Add or remove entries from the `buses` array freely.

---

## How to Change a Weight

Open the scenario JSON and edit the `weights` block:

```json
"weights": {
    "individual": 1.0,
    "operator": 2.0,
    "overall": 1.0
}
```

That's the only place weights exist. The scheduler reads them at runtime. No code change, no restart needed beyond reloading the app.

---

## How to Add a New Soft Rule

**Step 1 — Define the rule in `scheduler.py`:**

```python
def _rule_electricity_cost(
    self,
    bus: Bus,
    station_id: str,
    arrival_time: float,
    charger_free_time: float
) -> float:
    """
    Penalise charging during peak electricity pricing hours.
    Pricing windows are defined in the scenario JSON under 'pricing'.
    """
    pricing = self.scenario.get("pricing", [])
    charge_start = max(arrival_time, charger_free_time)

    for window in pricing:
        window_start = parse_time(window["from"])
        window_end = parse_time(window["to"])
        if window_start <= charge_start < window_end:
            return window["cost_per_min"] * self.physics.charge_time_min * self.weights.overall

    return 0.0
```

**Step 2 — Register it in `__init__`:**

```python
self.soft_rules = [
    self._rule_individual_wait,
    self._rule_operator_fairness,
    self._rule_overall_time,
    self._rule_electricity_cost,   # <-- new rule added here
]
```

The engine's `_score_candidate` method calls every rule in this list automatically. Nothing else changes.

---

## Code Structure

```
scheduler.py
├── Data classes         — Segment, Station, Physics, Weights, Bus, ChargeEvent, BusResult
├── Scheduler.__init__   — Parses scenario, initialises charger slots, registers soft rules
├── Route helpers        — _stops(), _segs(), _charging_stations_in_order(), _travel_time()
├── Charging plan        — _valid_charging_stops() — greedy hard constraint solver
├── Soft rules           — _rule_individual_wait, _rule_operator_fairness, _rule_overall_time
├── Score aggregator     — _score_candidate() — sums all soft rule outputs
├── Charger allocation   — _allocate_charger() — picks best slot, books it
├── Bus simulation       — _simulate_bus() — walks one bus through its full route
└── run()                — sorts buses by departure, simulates all, returns results

scenario_loader.py
├── load_scenario()      — loads and validates one JSON file
├── load_all_scenarios() — loads all scenario_*.json from a directory
├── parse_time()         — "19:30" → 1170.0 (minutes from midnight)
└── fmt_time()           — 1170.0 → "19:30"

app.py
├── Scenario picker      — dropdown populated from scenarios/ directory
├── Scenario input view  — route, stations, physics, weights, bus table
├── Per-bus timetable    — full timeline per bus with charge events and wait times
└── Per-station view     — charging order at each station
```

---

## Assumptions

- Every bus departs with a full charge. Origin endpoints (Bengaluru, Kochi) have slow overnight chargers and are not part of the scheduling problem.
- Charging always fills to full. Partial charging is not modelled.
- Buses travel at constant speed throughout their journey. Speed is a per-scenario value in `physics`.
- The greedy charging strategy (delay charging as long as safely possible) is preferred over eager charging because it minimises stops and keeps buses moving, which generally produces lower total journey times.
- Buses are processed in strict departure order. This is a simple and defensible priority — the bus that left earlier has the stronger claim to infrastructure.
- When a station has multiple chargers, the scheduler scores each slot independently and picks the best one. All chargers at a station are treated as equivalent.
- Operator fairness is computed from buses already completed at the time a new bus is being scheduled. This means earlier buses slightly underrepresent their operator's average — an acceptable approximation for the scale of this problem.
- Scenarios are independent. Charger state does not persist between scenarios.