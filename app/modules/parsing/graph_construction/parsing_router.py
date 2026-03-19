from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.database import get_async_db, get_db
from app.modules.auth.auth_service import AuthService
from app.modules.parsing.graph_construction.parsing_controller import ParsingController
from app.modules.parsing.graph_construction.parsing_schema import (
    LinkProjectsRequest,
    LinkProjectsResponse,
    ParsingRequest,
    ParsingStatusRequest,
)
from app.modules.utils.APIRouter import APIRouter

router = APIRouter()


@router.post("/parse")
async def parse_directory(
    repo_details: ParsingRequest,
    db: Session = Depends(get_db),
    user=Depends(AuthService.check_auth),
):
    return await ParsingController.parse_directory(repo_details, db, user)


@router.get("/parsing-status/{project_id}")
async def get_parsing_status(
    project_id: str,
    db: Session = Depends(get_db),
    async_db: AsyncSession = Depends(get_async_db),
    user=Depends(AuthService.check_auth),
):
    return await ParsingController.fetch_parsing_status(
        project_id, db, async_db, user
    )


@router.post("/parsing-status")
async def get_parsing_status_by_repo(
    request: ParsingStatusRequest,
    db: Session = Depends(get_db),
    user=Depends(AuthService.check_auth),
):
    return await ParsingController.fetch_parsing_status_by_repo(request, db, user)


@router.post("/link-projects")
async def link_projects(
    request: LinkProjectsRequest,
    db: Session = Depends(get_db),
    user=Depends(AuthService.check_auth),
):
    from app.core.config_provider import ConfigProvider
    from app.modules.parsing.graph_construction.cross_project_linker import CrossProjectLinker
    neo4j_config = ConfigProvider().get_neo4j_config()
    linker = CrossProjectLinker(
        neo4j_uri=neo4j_config["uri"],
        neo4j_username=neo4j_config["username"],
        neo4j_password=neo4j_config["password"],
    )
    try:
        strategies = linker.link_projects(request.project_ids)
        total = sum(strategies.values())
        return LinkProjectsResponse(linked=total, strategies=strategies)
    finally:
        linker.close()
