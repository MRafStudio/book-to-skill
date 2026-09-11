"""Plugin registry: discovery and ordered selection.

Built-in strategies live in ``generic.py``. Any further ``plugin_*.py`` file in
this directory is imported automatically -- that is the entire registration
step for a site-specific processor.
"""
from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path

from book_to_skill.plugins.base import FetchResult, Plugin  # noqa: F401

_SITE_PLUGIN_PREFIX = "plugin_"


def _collect(module) -> list:
    """Instantiate every Plugin subclass *defined in* this module."""
    found = []
    for obj in vars(module).values():
        if (
            isinstance(obj, type)
            and issubclass(obj, Plugin)
            and obj is not Plugin
            and obj.__module__ == module.__name__
        ):
            found.append(obj())
    return found


def load_plugins() -> list:
    """Return plugin instances: built-ins first, then discovered site plugins."""
    modules = [importlib.import_module("book_to_skill.plugins.generic")]
    for mod in pkgutil.iter_modules([str(Path(__file__).parent)]):
        if mod.name.startswith(_SITE_PLUGIN_PREFIX):
            modules.append(importlib.import_module(f"book_to_skill.plugins.{mod.name}"))
    plugins = []
    for module in modules:
        plugins.extend(_collect(module))
    return plugins
