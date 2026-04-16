from __future__ import annotations

import importlib.util
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def _load_engine():
    engine_path = Path(__file__).resolve().parents[2] / 'tn_rules.py'
    spec = importlib.util.spec_from_file_location('buildsmart_tn_rules', engine_path)
    if spec is None or spec.loader is None:
        raise ImportError(f'Could not load tn_rules.py from {engine_path}')

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def get_engine():
    return _load_engine()


def check_compliance(fields: dict) -> dict:
    return get_engine().check_compliance(fields)
