"""Battery energy storage system sizing model.

This module wraps in a small object-oriented API the original prototype optimization model.
The formulation keeps the same behavior as the prototype:

- battery power and energy capacity are decision variables
- solar generation is dispatchable up to an availability profile
- state of charge is cyclic over the full horizon
- unmet load is penalized heavily

Typical usage:

    import bess_model as bm

    model = bm.Model()
    model.add_solar(capacity=100, profile=solar_capacity_factors)
    model.add_storage(efficiency=0.9)
    model.add_load(demand=demand_data)
    model.solve()
    results = model.get_results()
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

import matplotlib.pyplot as plt
from matplotlib.figure import Figure
import numpy as np
import linopy


ArrayLike1D = Sequence[float] | np.ndarray[Any, Any]


@dataclass
class SolarSpec:
    capacity: float
    profile: np.ndarray


@dataclass
class StorageSpec:
    efficiency: float
    battery_power_cost: float = 200000.0
    battery_energy_cost: float = 300000.0
    max_battery_power: float = 200.0
    max_hours: float = 4.0


@dataclass
class LoadSpec:
    demand: np.ndarray


class Model:
    """BESS sizing and dispatch model.

    The model is intentionally simple and keeps the same structure as the
    original prototype, but the workflow is split into clear stages:

    1. add_solar(...)
    2. add_storage(...)
    3. add_load(...)
    4. solve()
    5. get_results()

    Storage is sized endogenously by the optimization problem. The optional
    power/energy inputs in add_storage are treated as sizing bounds when supplied,
    which makes the class flexible for both optimization and later simulation use.
    """

    def __init__(self, solver_name: str = "highs", unmet_penalty: float = 1e9) -> None:
        self.solver_name = solver_name
        self.unmet_penalty = float(unmet_penalty)
        self._solar: Optional[SolarSpec] = None
        self._storage: Optional[StorageSpec] = None
        self._load: Optional[LoadSpec] = None
        self._model: Optional[linopy.Model] = None

    @staticmethod
    def _to_1d_array(values: ArrayLike1D, name: str) -> np.ndarray:
        array = np.asarray(values, dtype=float)
        if array.ndim != 1:
            raise ValueError(f"{name} must be a one-dimensional sequence")
        if len(array) == 0:
            raise ValueError(f"{name} cannot be empty")
        return array

    def add_solar(self, capacity: float, profile: ArrayLike1D) -> "Model":
        """Register a solar farm.

        Parameters
        ----------
        capacity:
            Installed solar capacity in MW.
        profile:
            Hourly capacity factors or availability profile.
        """

        if capacity < 0:
            raise ValueError("capacity must be non-negative")
        self._solar = SolarSpec(capacity=float(capacity), profile=self._to_1d_array(profile, "profile"))
        return self

    def add_storage(
        self,
        power_capacity: Optional[float] = None,
        energy_capacity: Optional[float] = None,
        efficiency: float = 0.9,
        battery_power_cost: float = 200000.0,
        battery_energy_cost: float = 300000.0,
        max_battery_power: Optional[float] = None,
        max_hours: Optional[float] = None,
    ) -> "Model":
        """Register storage sizing assumptions.

        The optimization still sizes the battery endogenously. If explicit bounds
        are not supplied, the method falls back to the prototype defaults. When
        `power_capacity` and `energy_capacity` are supplied, they are used to derive
        the upper bounds if no explicit bounds are given.
        """

        if not (0 < efficiency <= 1):
            raise ValueError("efficiency must be in the interval (0, 1]")

        if max_battery_power is None:
            max_battery_power = float(power_capacity) if power_capacity is not None else 200.0
        if max_hours is None:
            if power_capacity is not None and energy_capacity is not None and power_capacity > 0:
                max_hours = float(energy_capacity) / float(power_capacity)
            else:
                max_hours = 4.0

        if max_battery_power < 0:
            raise ValueError("max_battery_power must be non-negative")
        if max_hours <= 0:
            raise ValueError("max_hours must be positive")

        self._storage = StorageSpec(
            efficiency=float(efficiency),
            battery_power_cost=float(battery_power_cost),
            battery_energy_cost=float(battery_energy_cost),
            max_battery_power=float(max_battery_power),
            max_hours=float(max_hours),
        )
        return self

    def add_load(self, demand: ArrayLike1D) -> "Model":
        """Register demand timeseries."""

        self._load = LoadSpec(demand=self._to_1d_array(demand, "demand"))
        return self

    def _validate_ready(self) -> None:
        if self._solar is None:
            raise RuntimeError("Solar data has not been added")
        if self._storage is None:
            raise RuntimeError("Storage data has not been added")
        if self._load is None:
            raise RuntimeError("Load data has not been added")

        if len(self._solar.profile) != len(self._load.demand):
            raise ValueError("Solar profile and demand must have the same length")

    def _build_model(self) -> linopy.Model:
        self._validate_ready()

        solar = self._solar
        storage = self._storage
        load = self._load
        assert solar is not None and storage is not None and load is not None

        # define simulation timesteps based on load length
        timesteps = range(len(load.demand))
        # define the optimization model
        model = linopy.Model()

        ### Decision variables (battery power and energy capacity)
        battery_power_capacity = model.add_variables(
            lower=0,
            upper=storage.max_battery_power,
            name="battery_power_capacity",
        )
        battery_energy_capacity = model.add_variables(lower=0, name="battery_energy_capacity")

        # add duration constraint
        model.add_constraints(
            battery_energy_capacity <= storage.max_hours * battery_power_capacity,
            name="battery_duration_limit",
        )

        ### Create time-dependent variables and constraints

        # solar generation variables and constraints
        solar_generation = model.add_variables(lower=0, coords=[timesteps], name="sol")
        for t in timesteps:
            model.add_constraints(
                solar_generation[t] <= solar.capacity * solar.profile[t],
                name=f"sol_limit_{t}",
            )

        # storage variables
        charge = model.add_variables(lower=0, coords=[timesteps], name="s_charge")
        discharge = model.add_variables(lower=0, coords=[timesteps], name="s_discharge")
        soc = model.add_variables(lower=0, coords=[timesteps], name="s_soc")
        unmet = model.add_variables(lower=0, coords=[timesteps], name="unmet")
        # storage constraints
        model.add_constraints(charge <= battery_power_capacity, name="charge_limit")
        model.add_constraints(discharge <= battery_power_capacity, name="discharge_limit")
        model.add_constraints(soc <= battery_energy_capacity, name="soc_limit")

        # storage dynamics
        efficiency_sqrt = np.sqrt(storage.efficiency)
        for t in timesteps:
            if t == 0:
                continue
            model.add_constraints(
                soc[t]
                == soc[t - 1] + charge[t] * efficiency_sqrt - discharge[t] / efficiency_sqrt,
                name=f"soc_{t}",
            )

        # cyclic constraint (for t==0)
        model.add_constraints(
            soc[0] == soc[len(load.demand) - 1] + charge[0] * efficiency_sqrt - discharge[0] / efficiency_sqrt,
            name="cyclic_soc",
        )

        # energy balance constraints
        for t in timesteps:
            balance_expression = solar_generation[t] + discharge[t] - charge[t] + unmet[t]  # pyright: ignore[reportOperatorIssue]
            model.add_constraints(
                balance_expression == load.demand[t],
                name=f"balance_{t}",
            )

        ### objective function
        objective = (
            storage.battery_power_cost * battery_power_capacity
            + storage.battery_energy_cost * battery_energy_capacity
            + (unmet * self.unmet_penalty).sum()
        )
        model.add_objective(objective)
        return model

    def solve(self) -> "Model":
        """Build and solve the optimization model."""

        self._model = self._build_model()
        self._model.solve(solver_name=self.solver_name)
        return self

    def get_results(self) -> Dict[str, Any]:
        """Extract results from the solved model and return them as a dictionary."""

        if self._model is None:
            raise RuntimeError("Model has not been solved yet")

        model = self._model
        objective_value = model.objective.value
        if objective_value is None:
            raise RuntimeError("Solver did not return an objective value")

        results: Dict[str, Any] = {
            "sol": model.solution["sol"].to_numpy(),
            "s_charge": model.solution["s_charge"].to_numpy(),
            "s_discharge": model.solution["s_discharge"].to_numpy(),
            "s_soc": model.solution["s_soc"].to_numpy(),
            "unmet": model.solution["unmet"].to_numpy(),
            "battery_power_capacity": float(model.solution["battery_power_capacity"]),
            "battery_energy_capacity": float(model.solution["battery_energy_capacity"]),
            "objective_value": float(objective_value),
        }
        results["total_unmet"] = float(results["unmet"].sum())
        return results

    def plot_dispatch(self, save_path: Optional[str] = None, show: bool = False) -> Figure:
        """Plot the solved dispatch and battery state of charge."""

        results = self.get_results()
        assert self._load is not None
        hours = np.arange(0, len(self._load.demand))

        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 10), sharex=True)
        ax1.plot(hours, self._load.demand, "k-", linewidth=2, label="Demand", marker="o")
        ax1.plot(hours, results["sol"], "y-", linewidth=2, label="Solar Generation", marker="s")
        ax1.plot(hours, results["s_discharge"], "g-", linewidth=2, label="Battery Discharge", marker="^")
        ax1.plot(hours, -results["s_charge"], "b-", linewidth=2, label="Battery Charge (negative)", marker="v")
        ax1.plot(hours, results["unmet"], "r--", linewidth=2, label="Unmet Load", marker="x")
        ax1.axhline(y=0, color="gray", linestyle="-", linewidth=0.5)
        ax1.set_ylabel("Power (MW)")
        ax1.set_title("Energy Dispatch")
        ax1.legend(loc="upper left")
        ax1.grid(True, alpha=0.3)

        ax2.fill_between(hours, 0, results["s_soc"], alpha=0.3, color="blue", label="Battery SOC")
        ax2.plot(hours, results["s_soc"], "b-", linewidth=2, marker="o")
        ax2.axhline(
            y=results["battery_energy_capacity"],
            color="r",
            linestyle="--",
            linewidth=1,
            label=f'Battery Capacity ({results["battery_energy_capacity"]:.1f} MWh)',
        )
        ax2.set_xlabel("Hour")
        ax2.set_ylabel("Energy (MWh)")
        ax2.set_title("Battery State of Charge")
        ax2.legend(loc="upper right")
        ax2.grid(True, alpha=0.3)
        ax2.set_xlim(-0.5, len(hours) - 0.5)

        plt.tight_layout()
        if save_path is not None:
            output_path = Path(save_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            fig.savefig(output_path, dpi=150, bbox_inches="tight")
        if show:
            plt.show()
        return fig

    def _format_report_text(self, include_hourly_details: bool = True) -> str:
        """Build the human-readable report text for console and file output."""

        results = self.get_results()
        assert self._load is not None and self._solar is not None

        duration_hours = results["battery_energy_capacity"] / max(results["battery_power_capacity"], 0.001)
        lines = [
            "=== Optimal Battery Sizing ===",
            f"Battery power capacity: {results['battery_power_capacity']:.2f} MW",
            f"Battery energy capacity: {results['battery_energy_capacity']:.2f} MWh",
            f"Battery duration: {duration_hours:.2f} hours",
            "",
            "=== System Performance ===",
            f"Objective value: ${results['objective_value']:,.2f}",
            f"Solar generation: {results['sol'].sum():.2f} MWh",
            f"Battery discharge: {results['s_discharge'].sum():.2f} MWh",
            f"Battery charge: {results['s_charge'].sum():.2f} MWh",
            f"Unmet load: {results['total_unmet']:.2f} MWh",
        ]

        if include_hourly_details:
            lines.append("")
            lines.append("=== Hourly Details ===")
            lines.append("Hour | Demand | Solar Max | Solar Gen | Charge | Discharge | SOC | Unmet")
            lines.append("-" * 80)
            for t in range(len(self._load.demand)):
                solar_max = self._solar.capacity * self._solar.profile[t]
                lines.append(
                    f"{t:4d} | {self._load.demand[t]:6.1f} | {solar_max:9.1f} | "
                    f"{results['sol'][t]:9.2f} | {results['s_charge'][t]:6.2f} | "
                    f"{results['s_discharge'][t]:9.2f} | {results['s_soc'][t]:6.2f} | {results['unmet'][t]:5.2f}"
                )

        return "\n".join(lines)

    def report(
        self,
        save_plot_path: Optional[str] = None,
        show_plot: bool = False,
        save_text_path: Optional[str] = "results/model_report.txt",
        include_hourly_details: bool = True,
    ) -> Figure:
        """Print report text, optionally save it to txt, and generate the dispatch plot."""

        report_text = self._format_report_text(include_hourly_details=include_hourly_details)
        print("\n" + "=" * 40)
        print(report_text)
        print("=" * 40 + "\n")

        if save_text_path is not None:
            report_path = Path(save_text_path)
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(report_text + "\n", encoding="utf-8")

        return self.plot_dispatch(save_path=save_plot_path, show=show_plot)
