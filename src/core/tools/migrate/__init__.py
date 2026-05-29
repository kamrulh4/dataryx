"""
Dataryx Migration Tool

Converts old pickle-based .dataryx format to new YAML format.

Usage:
    python -m tools.migrate <path>
    python -m tools.migrate old_flow.dataryx
    python -m tools.migrate ./flows/  # migrate entire directory
"""

__version__ = "1.0.0"
