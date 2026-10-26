from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.config import Settings, get_settings

router = APIRouter()


class SettingsStatus(BaseModel):
    model_configured: bool
    model_name: str
    base_url_host: str


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": "0.1.0"}


@router.get("/settings/status", response_model=SettingsStatus)
def settings_status(
    settings: Annotated[Settings, Depends(get_settings)],
) -> SettingsStatus:
    host = ""

    try:
        url = urlsplit(settings.model_base_url.strip())
        if (
            url.scheme in {"http", "https"}
            and url.hostname
            and url.username is None
            and url.password is None
        ):
            host = url.hostname
    except ValueError:
        host = ""

    key = settings.model_api_key.get_secret_value().strip()
    model_name = settings.model_name.strip()

    return SettingsStatus(
        model_configured=bool(host and model_name and key and key != "replace_me"),
        model_name=model_name,
        base_url_host=host,
    )
