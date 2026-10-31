"""Importing this package registers every audit check (order of import = registry order)."""

from . import covers, files, history, instrumental, matching, state  # noqa: F401
