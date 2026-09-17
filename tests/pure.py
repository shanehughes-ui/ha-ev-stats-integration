"""Load the modules that do not need Home Assistant, without loading the rest.

`trapezoid.py`, `classify.py` and `rolling.py` are deliberately free of any
Home Assistant import so the arithmetic and the judgement can be tested on
their own. Importing them the normal way would defeat that: `import
custom_components.ev_stats.classify` runs the package's `__init__.py`, which
pulls in Home Assistant and voluptuous and needs a whole test harness standing
up behind it.

So a stand-in package is registered whose `__path__` points at the component
directory and whose body does nothing. Relative imports inside these modules
then resolve against it - `from .const import ...` finds `const.py` and nothing
else - which is what lets them keep sharing the real constants instead of
duplicating them here, where the copy could drift.

If loading one of these ever starts requiring Home Assistant, that is the
separation being lost, and the import error is the point.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

PACKAGE = "ev_stats_pure"
SOURCE = Path(__file__).resolve().parents[1] / "custom_components" / "ev_stats"


def _package() -> types.ModuleType:
    if PACKAGE not in sys.modules:
        stub = types.ModuleType(PACKAGE)
        stub.__path__ = [str(SOURCE)]
        sys.modules[PACKAGE] = stub
    return sys.modules[PACKAGE]


def load(name: str) -> types.ModuleType:
    """Import one module by path, as a submodule of the stand-in package."""
    _package()
    full = f"{PACKAGE}.{name}"
    if full in sys.modules:
        return sys.modules[full]
    spec = importlib.util.spec_from_file_location(full, SOURCE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[full] = module
    spec.loader.exec_module(module)
    return module
