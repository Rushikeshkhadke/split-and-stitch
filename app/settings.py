import os
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[1]

# On Vercel serverless environments, only /tmp is writable
def get_default_storage_dir() -> Path:
    if os.environ.get("VERCEL") or os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
        return Path(tempfile.gettempdir()) / "jobs"
    return ROOT / "data" / "jobs"


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
        default_factory=get_default_storage_dir,
        validation_alias=AliasChoices("STORAGE_DIR", "WORK_DIR")
    )
    mode: Literal["mock", "real", "hf_space"] = Field("hf_space", validation_alias="MODE")
    max_segment_seconds: float = 4.0
    max_generation_side: int = 512
    segment_retries: int = 2
    default_max_duration: int = 2
    default_resolution: str = "Low Res"

settings = Settings()
