from __future__ import annotations

import inspect

from runtime.execution import execute_dynamic_request


def test_execution_package_wrapper_accepts_event_bus():
    signature = inspect.signature(execute_dynamic_request)

    assert "event_bus" in signature.parameters
