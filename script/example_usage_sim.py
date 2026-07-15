"""
Battery Simulator
Example usage (predifined case study) for testing and demonstration purposes
"""
import os, sys
from pathlib import Path
import numpy as np

# Ensure repository root is on sys.path when running from scripts/.
repo_root = Path.cwd()
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from src.bess_simulator import Simulator

# Example input data for a 24-hour period
demand = np.array([
    50,
    45,
    40,
    38,
    36,
    38,
    45,
    60,
    75,
    85,
    90,
    92,
    90,
    88,
    86,
    88,
    92,
    95,
    90,
    80,
    70,
    65,
    58,
    52,
])
solar_capacity = 300
solar_profile = np.array([
    0,
    0,
    0,
    0,
    0,
    0,
    0.1,
    0.3,
    0.5,
    0.7,
    0.85,
    0.95,
    0.98,
    0.95,
    0.88,
    0.75,
    0.55,
    0.3,
    0.1,
    0,
    0,
    0,
    0,
    0,
])

def main() -> None:
    """Run the module's built-in example."""

    # Chosen battery parameters for the simulation
    battery_power_capacity = 85.91  # MW
    battery_energy_capacity = 687.27  # MWh

    print("\n" + "=" * 40)
    print("\nRunning battery simulation with example data...")
    # Create and solve the model
    model = Simulator(battery_power_capacity, battery_energy_capacity, efficiency=0.9)
    model.add_solar(capacity=solar_capacity, profile=solar_profile)
    model.add_load(demand=demand)
    model.simulate()
    model.report(save_plot_path="results/demo/dispatch_plot_sim.png")


if __name__ == "__main__":
    main()
