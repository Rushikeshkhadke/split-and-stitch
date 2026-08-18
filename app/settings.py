import os
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[1]


def get_writable_storage_dir(candidate: Path | str | None = None) -> Path:
    if os.environ.get("VERCEL") or os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
        tmp_dir = Path(tempfile.gettempdir()) / "character_swap_jobs"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        return tmp_dir
    
    target = Path(candidate) if candidate else ROOT / "data" / "jobs"
    try:
        target.mkdir(parents=True, exist_ok=True)
        # Test write permission
        test_file = target / ".write_test"
        test_file.touch()
        test_file.unlink()
        return target
    except Exception:
        fallback = Path(tempfile.gettempdir()) / "character_swap_jobs"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    hf_space_url: str = Field(
        "https://alexnasa-wan2-2-animate-zerogpu.hf.space",
        validation_alias=AliasChoices("HF_SPACE_URL", "SPACE_URL")
    )
    hf_token: str | None = Field(
        None,
        validation_alias=AliasChoices("HF_TOKEN", "HUGGINGFACE_TOKEN", "HUGGING_FACE_HUB_TOKEN")
    )
    comfyui_url: str = Field(
        "http://127.0.0.1:8188",
        validation_alias=AliasChoices("COMFYUI_URL", "COMFY_URL")
    )
    wan_workflow_path: Path = Field(
        ROOT / "workflow" / "wan22_animate_mix_api.json",
        validation_alias=AliasChoices("WAN_WORKFLOW_PATH", "COMFY_WORKFLOW_PATH")
    )
    storage_dir: Path = Field(
        default_factory=get_writable_storage_dir,
        validation_alias=AliasChoices("STORAGE_DIR", "WORK_DIR")
    )
    mode: Literal["mock", "real", "hf_space"] = Field("hf_space", validation_alias="MODE")
    max_segment_seconds: float = 4.0
    max_generation_side: int = 512
    segment_retries: int = 2
    default_max_duration: int = 2
    default_resolution: str = "Low Res"

    @field_validator("storage_dir", mode="after")
    @classmethod
    def validate_storage_dir(cls, v: Path) -> Path:
        return get_writable_storage_dir(v)


settings = Settings()
