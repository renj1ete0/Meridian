"""Shared core for Meridian services.

Anything touching the database lives here. Services import from this package;
they never define their own models (AGENTS.md).
"""

__version__ = "0.16.0"
