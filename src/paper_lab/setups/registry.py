"""Setup registry. Adding a setup = adding ONE module to this package:

    from paper_lab.setups.base import DayState, Setup, SetupParams
    from paper_lab.setups.registry import register

    @register
    class MySetup(Setup[MyState]):
        name = "my_setup"
        params_model = MyParams
        ...

plus a ``setups.my_setup`` entry in config.yaml. Modules are discovered automatically.
"""

from __future__ import annotations

import importlib
import pkgutil
from typing import Any

from paper_lab.config import Config, SetupConfig
from paper_lab.setups.base import Setup

_REGISTRY: dict[str, type[Setup[Any]]] = {}
_INFRA_MODULES = {"base", "registry", "runner"}


def register[T: type[Setup[Any]]](cls: T) -> T:
    name = getattr(cls, "name", None)
    if not isinstance(name, str) or not name:
        raise TypeError(f"{cls.__name__} must define a non-empty class attribute 'name'")
    if name in _REGISTRY and _REGISTRY[name] is not cls:
        raise ValueError(f"setup name {name!r} registered twice")
    _REGISTRY[name] = cls
    return cls


def _discover() -> None:
    import paper_lab.setups as pkg

    for m in pkgutil.iter_modules(pkg.__path__):
        if m.name not in _INFRA_MODULES and not m.name.startswith("_"):
            importlib.import_module(f"{pkg.__name__}.{m.name}")


def registry() -> dict[str, type[Setup[Any]]]:
    _discover()
    return dict(_REGISTRY)


def build_setup(name: str, sc: SetupConfig) -> Setup[Any]:
    reg = registry()
    if name not in reg:
        raise KeyError(f"unknown setup {name!r}; registered: {sorted(reg)}")
    return reg[name].from_params(sc.params, sc.interval)


def build_all(cfg: Config, enabled_only: bool = True) -> dict[str, Setup[Any]]:
    """Validate every configured setup (also disabled ones) and return the requested ones."""
    built = {name: build_setup(name, sc) for name, sc in cfg.setups.items()}
    return {n: s for n, s in built.items() if cfg.setups[n].enabled or not enabled_only}
