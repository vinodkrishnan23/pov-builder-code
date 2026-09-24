"""Checkpoint serialization allow-list.

LangGraph's default `JsonPlusSerializer` currently allows deserializing any
type from a checkpoint, with a deprecation warning — a future LangGraph
version will BLOCK unregistered types entirely (`LANGGRAPH_STRICT_MSGPACK`).
Since `POVState` is full of our own Pydantic models and enums, we allow-list
them explicitly now rather than waiting for that to become a hard failure.

The allow-list is built by introspection (every `BaseModel`/`Enum` subclass
actually defined under `pov_builder.models`), not a manually-maintained
list — so a new model added in a later phase is covered automatically
without anyone needing to remember to update this file.
"""

from __future__ import annotations

import enum
import importlib
import pkgutil
from typing import Iterable

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from pydantic import BaseModel

import pov_builder.models as _models_pkg


def _discover_checkpointable_types() -> list[type]:
    types: list[type] = []
    for module_info in pkgutil.iter_modules(_models_pkg.__path__, prefix=f"{_models_pkg.__name__}."):
        module = importlib.import_module(module_info.name)
        for name in dir(module):
            obj = getattr(module, name)
            if not isinstance(obj, type) or obj.__module__ != module.__name__:
                continue
            if issubclass(obj, (BaseModel, enum.Enum)):
                types.append(obj)
    return types


def build_checkpoint_serializer() -> JsonPlusSerializer:
    return JsonPlusSerializer(allowed_msgpack_modules=_discover_checkpointable_types())


def discover_checkpointable_types() -> Iterable[type]:
    """Exposed for tests — confirms the allow-list actually covers every
    model/enum currently defined, not just a fixed snapshot."""
    return _discover_checkpointable_types()
