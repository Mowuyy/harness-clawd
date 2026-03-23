# -*- coding: utf-8 -*-
from contextvars import ContextVar
from typing import Any


class Context:

    def __init__(self, name='default_context') -> None:
        self._common_ctx_var: ContextVar[dict] = ContextVar(name)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            data = self._common_ctx_var.get()
        except LookupError:
            return default
        return data.get(key, default)

    def set(self, key: str, val: Any) -> None:
        try:
            data = self._common_ctx_var.get()
        except LookupError:
            data = {}
            self._common_ctx_var.set(data)
        data[key] = val

    def remove(self, key: str) -> None:
        try:
            data = self._common_ctx_var.get()
            if key in data:
                data.pop(key)
        except LookupError:
            pass

    def reset(self) -> None:
        self._common_ctx_var.set({})


context = Context()
