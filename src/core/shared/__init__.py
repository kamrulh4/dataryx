"""
Shared utilities for Dataryx services.
This package contains common functionality that can be used across
dataryx_core, dataryx_worker, and other components without creating
circular dependencies.

CLI Examples:
  # Start the Dataryx UI
  Dataryx run ui

  # Run a flow from a file
  Dataryx run flow my_pipeline.yaml

  # Advanced: Run individual components
  Dataryx run core  # Start only the core service
  Dataryx run worker  # Start only the worker service

  # Options
  Dataryx run ui --host 0.0.0.0 --port 8080  # Custom host/port
  Dataryx run ui --no-browser  # Don't open browser
"""

from .storage_config import get_cache_directory, get_flows_directory, get_temp_directory, storage

__all__ = ["storage", "get_cache_directory", "get_temp_directory", "get_flows_directory"]
