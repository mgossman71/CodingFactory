"""FastAPI app assembly."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .api.routes import router
from .api.ws import ws_router
from .config import Settings, get_settings
from .db import Base, make_engine, make_session_factory
from .llm.gateway import LLMGateway
from .services.build import BuildContext
from .services.discovery import DiscoveryContext
from .services.events import EventBus
from .services.opencode import MockExecutor, OpenCodeExecutor
from .services.runner import Runner

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("cf")


def create_app(
    settings: Settings | None = None,
    *,
    engine=None,
    llm=None,
    executor=None,
) -> FastAPI:
    settings = settings or get_settings()
    if engine is None:
        from pathlib import Path

        Path(settings.data_dir).mkdir(parents=True, exist_ok=True)
        engine = make_engine(settings.database_url)
    sessions = make_session_factory(engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        Base.metadata.create_all(engine)
        await app.state.runner.resume()
        yield
        runner_: Runner = app.state.runner
        await runner_.shutdown()
        if isinstance(app.state.llm, LLMGateway):
            await app.state.llm.aclose()

    app = FastAPI(title="CodingFactory Lite", lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = engine
    app.state.sessions = sessions
    app.state.llm = llm or LLMGateway(settings)
    app.state.bus = EventBus()
    if executor is not None:
        app.state.executor = executor
    elif settings.opencode_bin == "mock":
        app.state.executor = MockExecutor()
    else:
        app.state.executor = OpenCodeExecutor(settings)

    app.state.discovery_ctx = DiscoveryContext(
        sessions=sessions, llm=app.state.llm, bus=app.state.bus, settings=settings
    )
    app.state.build_ctx = BuildContext(
        sessions=sessions, llm=app.state.llm, executor=app.state.executor, bus=app.state.bus, settings=settings
    )
    app.state.runner = Runner(app.state.discovery_ctx, app.state.build_ctx)

    @app.get("/healthz")
    def healthz():
        return {"status": "ok", "model": settings.llm_model}

    app.include_router(router)
    app.include_router(ws_router)

    # Serve the built frontend when present (production/compose mode).
    from pathlib import Path as _Path

    dist = _Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if dist.exists():
        from fastapi.staticfiles import StaticFiles

        app.mount("/", StaticFiles(directory=dist, html=True), name="web")

    return app


app = create_app()
