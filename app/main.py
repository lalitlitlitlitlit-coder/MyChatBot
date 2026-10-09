"""Tennis mental performance chatbot (v1 prototype): app assembly."""
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .bots import Bots
from .config import Settings
from .db import Database
from .identity import AttemptLimiter
from .model_client import build_model_client
from .routes import review, tester


def create_app(settings: Settings) -> FastAPI:
    app = FastAPI(title="Tennis mental performance chatbot", docs_url=None, redoc_url=None)
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    app.state.settings = settings
    app.state.db = Database(settings.database_path)
    app.state.limiter = AttemptLimiter()
    app.state.model = build_model_client(settings)
    app.state.bots = Bots(settings.bots_dir)

    app.include_router(tester.router)
    app.include_router(review.router)

    @app.get("/", include_in_schema=False)
    def tester_page():
        return FileResponse(settings.static_dir / "index.html")

    @app.get("/review", include_in_schema=False)
    def review_page():
        return FileResponse(settings.static_dir / "review.html")

    app.mount("/static", StaticFiles(directory=settings.static_dir), name="static")
    return app
