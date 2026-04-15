"""Version endpoint routes."""

import asyncio
import logging
from importlib.metadata import PackageNotFoundError, version

from fastapi import APIRouter
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter()


class VersionResponse(BaseModel):
    version: str


@router.get("/version", response_model=VersionResponse)
async def get_version() -> VersionResponse:
    """
    Return the installed package version for the backend.

    The version is read from the dynachat-backend package metadata.
    Falls back to "0.1.0" if the package is not installed.
    """
    try:
        ver = await asyncio.to_thread(version, "dynachat-backend")
    except PackageNotFoundError:
        ver = "0.1.0"
    except Exception as exc:
        logger.warning("Unexpected error reading package version: %s", exc)
        raise
    return VersionResponse(version=ver)
