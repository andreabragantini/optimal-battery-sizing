"""
Optimal Battery Sizing for Solar Energy Systems
Example usage (predifined case study) for testing and demonstration purposes
"""
import os, sys
from pathlib import Path
import numpy as np

# Ensure repository root is on sys.path when running from scripts/.
repo_root = Path.cwd()
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from src.bess_optimizer import Optimizer


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

    print("\n" + "=" * 40)
    print("\nRunning optimal battery sizing model with example data...")
    # Create and solve the model
    model = Optimizer()
    # Define battery parameters
    model.add_storage(
        efficiency=0.9,
        battery_power_cost=200000,
        battery_energy_cost=300000,
        max_battery_power=500,
        max_hours=8,
    )
    model.add_solar(capacity=solar_capacity, profile=solar_profile)
    model.add_load(demand=demand)
    model.solve()
    model.report(save_plot_path="results/demo/dispatch_plot_opt.png")


if __name__ == "__main__":
    main()
