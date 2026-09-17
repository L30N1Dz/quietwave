"""Shared FastAPI dependencies.

The store and the Twilio gateway are built once at startup and hung off
``app.state``, which keeps them swappable: the test suite installs a
MemoryStore and a DemoGateway without touching route code.
"""

from __future__ import annotations

from fastapi import Depends, Request

from app.config import Settings, get_settings
from app.store.base import Store
from app.twilio_client import Gateway


def get_store(request: Request) -> Store:
    return request.app.state.store


def get_gateway(request: Request) -> Gateway:
    return request.app.state.gateway


def get_config(request: Request) -> Settings:
    return getattr(request.app.state, "settings", None) or get_settings()


StoreDep = Depends(get_store)
GatewayDep = Depends(get_gateway)
ConfigDep = Depends(get_config)
