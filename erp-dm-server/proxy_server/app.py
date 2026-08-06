"""FastAPI application factory; it never opens or creates a campaign database."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .dependencies import default_storyteller
from .errors import TurnError
from .routes.chat import router


def create_app(campaign_session=None, storyteller=None) -> FastAPI:
    app = FastAPI(title="Zero Context AI RPG", version="2B")
    app.state.campaign_session = campaign_session
    app.state.storyteller = storyteller or default_storyteller()
    app.include_router(router)

    @app.exception_handler(TurnError)
    async def turn_error_handler(_request: Request, error: TurnError):
        return JSONResponse(status_code=error.http_status, content=error.payload())

    @app.exception_handler(RuntimeError)
    async def runtime_boundary(_request: Request, _error: RuntimeError):
        error = TurnError("no_active_campaign", "No active campaign session is configured.", "unavailable", False, 409)
        return JSONResponse(status_code=error.http_status, content=error.payload())

    return app
