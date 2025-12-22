from __future__ import annotations

import importlib.util
import os
import sys
from typing import Callable

_APP: Callable | None = None


def _load_app() -> Callable:
    global _APP
    if _APP is not None:
        return _APP

    backend_root = os.path.dirname(__file__)
    api_path = os.path.join(backend_root, "api.py")
    spec = importlib.util.spec_from_file_location("backend_api", api_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load backend api.py")
    if backend_root not in sys.path:
        sys.path.insert(0, backend_root)
    module = importlib.util.module_from_spec(spec)
    sys.modules["backend_api"] = module
    spec.loader.exec_module(module)
    _APP = module.create_app(init_db=False, seed_cases=False)
    return _APP


def app(environ, start_response):
    backend = _load_app()
    return backend(environ, start_response)
