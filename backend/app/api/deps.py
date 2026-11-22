"""Shared FastAPI dependencies / app state accessors."""

from fastapi import Request

from ..services.build import BuildContext
from ..services.discovery import DiscoveryContext
from ..services.events import EventBus
from ..services.runner import Runner


def bus(request: Request) -> EventBus:
    return request.app.state.bus


def runner(request: Request) -> Runner:
    return request.app.state.runner


def discovery_ctx(request: Request) -> DiscoveryContext:
    return request.app.state.discovery_ctx


def build_ctx(request: Request) -> BuildContext:
    return request.app.state.build_ctx
