from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, Request, Response, status
from fastapi.responses import JSONResponse

from app.bootstrap import build_demo_service
from app.config import Settings
from app.domain.errors import (
    AuthenticationError,
    AuthorizationError,
    IngestionConflictError,
)
from app.domain.models import (
    Chunk,
    DocumentInput,
    HealthResponse,
    IngestResponse,
    Principal,
    QueryRequest,
    QueryResponse,
)
from app.security.auth import (
    Authenticator,
    DemoAuthenticator,
    EntraJwtAuthenticator,
    bearer_token,
)
from app.service import KnowledgeService


def configure_observability(settings: Settings) -> None:
    if not settings.applicationinsights_connection_string:
        return
    try:
        from azure.monitor.opentelemetry import configure_azure_monitor
    except ImportError as exc:  # pragma: no cover - requires observability extra
        raise RuntimeError("install the 'observability' dependency extra") from exc
    configure_azure_monitor(
        connection_string=settings.applicationinsights_connection_string,
        logger_name="knowledge_platform",
    )


def build_authenticator(settings: Settings) -> Authenticator:
    if settings.auth_mode == "demo":
        return DemoAuthenticator()
    if settings.auth_mode == "entra":
        if not settings.azure_tenant_id or not settings.entra_audience:
            raise ValueError("AZURE_TENANT_ID and ENTRA_AUDIENCE are required for Entra auth")
        return EntraJwtAuthenticator(
            tenant_id=settings.azure_tenant_id,
            audience=settings.entra_audience,
            issuer=settings.entra_issuer,
        )
    raise ValueError(f"unsupported AUTH_MODE: {settings.auth_mode}")


def create_app(
    settings: Settings | None = None,
    service: KnowledgeService | None = None,
    authenticator: Authenticator | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.validate()
    if service is None:
        if settings.backend == "local":
            service = build_demo_service(settings)
        elif settings.backend == "azure":
            from app.azure_bootstrap import build_azure_service

            service = build_azure_service(settings)
        else:
            raise ValueError(f"unsupported BACKEND: {settings.backend}")
    authenticator = authenticator or build_authenticator(settings)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        logging.basicConfig(
            level=getattr(logging, settings.log_level.upper(), logging.INFO),
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
        )
        configure_observability(settings)
        application.state.settings = settings
        application.state.service = service
        application.state.authenticator = authenticator
        await service.initialize()
        yield

    application = FastAPI(
        title="Enterprise Knowledge Intelligence Platform",
        version="0.1.0",
        description="Security-trimmed, evidence-gated enterprise RAG reference API",
        lifespan=lifespan,
    )

    @application.exception_handler(AuthenticationError)
    async def authentication_error(_: Request, exc: AuthenticationError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": str(exc)},
            headers={"WWW-Authenticate": "Bearer"},
        )

    @application.exception_handler(AuthorizationError)
    async def authorization_error(_: Request, exc: AuthorizationError) -> JSONResponse:
        return JSONResponse(status_code=status.HTTP_403_FORBIDDEN, content={"detail": str(exc)})

    @application.exception_handler(IngestionConflictError)
    async def ingestion_conflict(_: Request, exc: IngestionConflictError) -> JSONResponse:
        return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={"detail": str(exc)})

    async def principal_dependency(
        request: Request, authorization: str | None = Header(default=None)
    ) -> Principal:
        token = bearer_token(authorization)
        return await request.app.state.authenticator.authenticate(token)

    def service_dependency(request: Request) -> KnowledgeService:
        return request.app.state.service

    @application.get("/health/live", response_model=HealthResponse, tags=["health"])
    async def live() -> HealthResponse:
        return HealthResponse(status="ok", checks={"process": "up"})

    @application.get("/health/ready", response_model=HealthResponse, tags=["health"])
    async def ready(
        response: Response, knowledge: KnowledgeService = Depends(service_dependency)
    ) -> HealthResponse:
        is_ready = await knowledge.ready()
        if not is_ready:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return HealthResponse(status="degraded", checks={"retrieval": "unavailable"})
        return HealthResponse(status="ok", checks={"retrieval": "ready"})

    @application.post("/v1/query", response_model=QueryResponse, tags=["knowledge"])
    async def query(
        payload: QueryRequest,
        request: Request,
        principal: Principal = Depends(principal_dependency),
        knowledge: KnowledgeService = Depends(service_dependency),
        x_demo_failure: str | None = Header(default=None, alias="X-Demo-Failure"),
    ) -> QueryResponse:
        failure_mode = None
        if (
            x_demo_failure
            and request.app.state.settings.allow_failure_injection
            and request.app.state.settings.environment in {"local", "test"}
        ):
            failure_mode = x_demo_failure
        return await knowledge.ask(
            payload.question,
            principal,
            debug=payload.debug,
            failure_mode=failure_mode,
        )

    @application.post(
        "/v1/ingest",
        response_model=IngestResponse,
        status_code=status.HTTP_200_OK,
        tags=["ingestion"],
    )
    async def ingest(
        payload: DocumentInput,
        principal: Principal = Depends(principal_dependency),
        knowledge: KnowledgeService = Depends(service_dependency),
    ) -> IngestResponse:
        return await knowledge.ingest(payload, principal)

    @application.get(
        "/v1/debug/chunks/{document_id}",
        response_model=list[Chunk],
        tags=["debug"],
    )
    async def debug_chunks(
        document_id: str,
        principal: Principal = Depends(principal_dependency),
        knowledge: KnowledgeService = Depends(service_dependency),
    ) -> list[Chunk]:
        return await knowledge.debug_chunks(document_id, principal)

    return application


app = create_app()
