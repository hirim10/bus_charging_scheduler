from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
from scenario_loader import parse_time, fmt_time


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Segment:
    from_stop: str
    to_stop: str
    distance_km: float


@dataclass
class Station:
    id: str
    chargers: int


@dataclass
class Physics:
    battery_range_km: float
    charge_time_min: float
    speed_kmh: float


@dataclass
class Weights:
    individual: float
    operator: float
    overall: float


@dataclass
class Bus:
    id: str
    operator: str
    direction: str
    departure: str


@dataclass
class ChargeEvent:
    station_id: str
    arrival_time: float
    wait_time: float
    charge_start: float
    charge_end: float


@dataclass
class BusResult:
    bus_id: str
    operator: str
    direction: str
    departure_time: float
    charge_events: List[ChargeEvent]
    arrival_time: float
    total_wait: float


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------

class Scheduler:
    def __init__(self, scenario: dict):
        self.scenario = scenario
        self.physics = Physics(**scenario["physics"])
        self.weights = Weights(**scenario["weights"])

        # Build segments
        self.segments: List[Segment] = [
            Segment(s["from"], s["to"], s["distance_km"])
            for s in scenario["route"]["segments"]
        ]

        # Stops in BK order
        self.stops_BK: List[str] = scenario["route"]["stops"]
        self.stops_KB: List[str] = list(reversed(self.stops_BK))

        # Stations dict
        self.stations: Dict[str, Station] = {
            s["id"]: Station(s["id"], s["chargers"])
            for s in scenario["stations"]
        }

        # Charger availability: station_id -> [free_time per charger slot]
        self.charger_free: Dict[str, List[float]] = {
            sid: [0.0] * s.chargers
            for sid, s in self.stations.items()
        }

        # Results accumulated during run — used by operator fairness rule
        self.results: List[BusResult] = []

        # ---------------------------------------------------------------------------
        # Soft rules registry
        # To add a new rule:
        #   1. Define a method _rule_yourname(self, bus, station_id, arrival_time, charger_free_time) -> float
        #   2. Append self._rule_yourname to this list
        # Nothing else changes.
        # ---------------------------------------------------------------------------
        self.soft_rules = [
            self._rule_individual_wait,
            self._rule_operator_fairness,
            self._rule_overall_time,
        ]

    # ---------------------------------------------------------------------------
    # Route helpers
    # ---------------------------------------------------------------------------

    def _stops(self, direction: str) -> List[str]:
        return self.stops_BK if direction == "BK" else self.stops_KB

    def _segs(self, direction: str) -> List[Segment]:
        return self.segments if direction == "BK" else list(reversed(self.segments))

    def _charging_stations_in_order(self, direction: str) -> List[str]:
        """Intermediate charging station IDs in route order for this direction."""
        return [s for s in self._stops(direction) if s in self.stations]

    def _travel_time(self, distance_km: float) -> float:
        """Minutes to travel a given distance at scenario speed."""
        return (distance_km / self.physics.speed_kmh) * 60

    # ---------------------------------------------------------------------------
    # Charging plan
    # Determines the minimum valid set of stations a bus must charge at,
    # using a greedy approach: delay charging as long as safely possible.
    # This keeps the bus moving and minimises unnecessary stops.
    # ---------------------------------------------------------------------------

    def _valid_charging_stops(self, direction: str) -> List[str]:
        """
        Returns the minimal list of charging station IDs the bus must stop at
        to never exceed battery range between any two consecutive charges.

        Strategy: greedy — drive as far as possible, only charge when forced.
        This minimises stops while guaranteeing the hard range constraint.
        """
        stops = self._stops(direction)
        segs = self._segs(direction)
        charging_stations = self._charging_stations_in_order(direction)
        max_range = self.physics.battery_range_km

        chosen: List[str] = []
        range_left = max_range
        last_charge_idx = 0  # index in stops[] of last charge point (origin = 0)

        for i in range(len(stops) - 1):
            seg_dist = segs[i].distance_km
            range_left -= seg_dist

            if range_left < 0:
                # Must have charged at some station between last_charge_idx and i
                # Pick the LATEST valid one (greedy — delays charging as long as possible)
                best: Optional[str] = None
                best_idx = -1
                for s in charging_stations:
                    idx = stops.index(s)
                    if last_charge_idx < idx <= i and idx > best_idx:
                        best = s
                        best_idx = idx

                if best is None:
                    raise ValueError(
                        f"[{direction}] No valid charging station between "
                        f"{stops[last_charge_idx]} and {stops[i + 1]}. "
                        f"Check battery range vs segment distances."
                    )

                chosen.append(best)

                # Recalculate remaining range from that station forward
                range_left = max_range
                for j in range(best_idx, i + 1):
                    range_left -= segs[j].distance_km

                last_charge_idx = best_idx

        return chosen

    # ---------------------------------------------------------------------------
    # Soft rules
    # Each rule receives (bus, station_id, arrival_time, charger_free_time)
    # and returns a float cost. Lower = better. Weights are applied here.
    #
    # To add a new rule, define a new method with this exact signature
    # and append it to self.soft_rules in __init__. The engine calls all
    # rules automatically via _score_candidate.
    # ---------------------------------------------------------------------------

    def _rule_individual_wait(
        self,
        bus: Bus,
        station_id: str,
        arrival_time: float,
        charger_free_time: float
    ) -> float:
        """
        Penalise how long THIS bus would have to wait at this charger slot.
        Weighted by individual weight.
        """
        wait = max(0.0, charger_free_time - arrival_time)
        return wait * self.weights.individual

    def _rule_operator_fairness(
        self,
        bus: Bus,
        station_id: str,
        arrival_time: float,
        charger_free_time: float
    ) -> float:
        """
        Penalise if this operator's buses have already accumulated high average wait.
        Encourages the scheduler to give relief to operators that are running behind.
        Weighted by operator weight.
        """
        op_waits = [
            r.total_wait for r in self.results
            if r.operator == bus.operator
        ]
        avg_op_wait = sum(op_waits) / len(op_waits) if op_waits else 0.0
        return avg_op_wait * self.weights.operator

    def _rule_overall_time(
        self,
        bus: Bus,
        station_id: str,
        arrival_time: float,
        charger_free_time: float
    ) -> float:
        """
        Penalise any additional wait time in the global network.
        Keeps total network delay low.
        Weighted by overall weight.
        """
        wait = max(0.0, charger_free_time - arrival_time)
        return wait * self.weights.overall

    def _score_candidate(
        self,
        bus: Bus,
        station_id: str,
        arrival_time: float,
        charger_free_time: float
    ) -> float:
        """
        Aggregate score across all soft rules.
        Lower is better. Used to pick the best charger slot.
        """
        return sum(
            rule(bus, station_id, arrival_time, charger_free_time)
            for rule in self.soft_rules
        )

    # ---------------------------------------------------------------------------
    # Charger allocation
    # ---------------------------------------------------------------------------

    def _allocate_charger(
        self,
        bus: Bus,
        station_id: str,
        arrival_time: float
    ) -> Tuple[float, float, float]:
        """
        Given a bus arriving at a station, pick the best available charger slot
        by scoring each slot with all soft rules and choosing the lowest score.

        Returns (wait_time, charge_start, charge_end).
        Updates charger_free to reflect the new booking.
        """
        slots = self.charger_free[station_id]

        best_slot_idx = min(
            range(len(slots)),
            key=lambda i: self._score_candidate(
                bus, station_id, arrival_time, slots[i]
            )
        )

        free_time = slots[best_slot_idx]
        charge_start = max(arrival_time, free_time)
        wait_time = charge_start - arrival_time
        charge_end = charge_start + self.physics.charge_time_min

        # Book the slot
        self.charger_free[station_id][best_slot_idx] = charge_end

        return wait_time, charge_start, charge_end

    # ---------------------------------------------------------------------------
    # Single bus simulation
    # ---------------------------------------------------------------------------

    def _simulate_bus(self, bus: Bus) -> BusResult:
        """
        Walk the bus along its route segment by segment.
        At each charging station it must visit, allocate a charger slot.
        Returns full BusResult with timeline.
        """
        stops = self._stops(bus.direction)
        segs = self._segs(bus.direction)
        charging_stops = self._valid_charging_stops(bus.direction)

        current_time = parse_time(bus.departure)
        charge_events: List[ChargeEvent] = []

        for i in range(len(stops) - 1):
            seg_dist = segs[i].distance_km
            current_time += self._travel_time(seg_dist)
            next_stop = stops[i + 1]

            if next_stop in charging_stops:
                wait, start, end = self._allocate_charger(bus, next_stop, current_time)
                charge_events.append(ChargeEvent(
                    station_id=next_stop,
                    arrival_time=current_time,
                    wait_time=wait,
                    charge_start=start,
                    charge_end=end
                ))
                current_time = end

        total_wait = sum(e.wait_time for e in charge_events)

        return BusResult(
            bus_id=bus.id,
            operator=bus.operator,
            direction=bus.direction,
            departure_time=parse_time(bus.departure),
            charge_events=charge_events,
            arrival_time=current_time,
            total_wait=total_wait
        )

    # ---------------------------------------------------------------------------
    # Run
    # ---------------------------------------------------------------------------

    def run(self) -> List[BusResult]:
        """
        Simulate all buses in the scenario.
        Buses are processed in departure order so earlier buses
        naturally get first access to chargers.
        """
        self.results = []

        buses = [Bus(**b) for b in self.scenario["buses"]]
        buses.sort(key=lambda b: parse_time(b.departure))

        for bus in buses:
            result = self._simulate_bus(bus)
            self.results.append(result)

        return self.results