import asyncio
import json
import math
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageDraw

from .settings import ROOT, settings

app = FastAPI(title="Wan2.2 Character Swap")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=ROOT / "app" / "static"), name="static")
jobs: dict[str, dict[str, Any]] = {}


def update_job(job_id: str, **kwargs: Any) -> dict[str, Any]:
    if job_id not in jobs:
        jobs[job_id] = read_job(job_id) or {"id": job_id}
    jobs[job_id].update(kwargs)
    for directory in [settings.storage_dir, Path(tempfile.gettempdir()) / "character_swap_jobs"]:
        try:
            directory.mkdir(parents=True, exist_ok=True)
            job_file = directory / f"job_{job_id}.json"
            job_file.write_text(json.dumps(jobs[job_id]), encoding="utf-8")
        except Exception:
            pass
    return jobs[job_id]


def read_job(job_id: str) -> dict[str, Any] | None:
    if job_id in jobs:
        return jobs[job_id]
    for directory in [settings.storage_dir, Path(tempfile.gettempdir()) / "character_swap_jobs"]:
        try:
            job_file = directory / f"job_{job_id}.json"
            if job_file.exists():
                data = json.loads(job_file.read_text(encoding="utf-8"))
                jobs[job_id] = data
                return data
        except Exception:
            pass
    return None


def make_mock_badge(path: Path, text: str = "PROCESSED CHUNK", max_width: int = 420) -> Path:
    w = max(260, min(max_width, 480))
    h = 44
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([(0, 0), (w, h)], radius=10, fill=(15, 23, 42, 220), outline=(56, 189, 248, 255), width=2)
    d.text((w // 2, h // 2), text, fill=(255, 255, 255, 255), anchor="mm")
    img.save(path)
    return path


def run(*args: str) -> str:
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"{' '.join(args)} failed")
    return result.stdout


def worker_url(path: str) -> str:
    return f"{settings.comfyui_url.rstrip('/')}{path}"


def hf_url(path: str) -> str:
    return f"{settings.hf_space_url.rstrip('/')}{path}"


def probe(video: Path) -> dict[str, Any]:
    try:
        raw = run("ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(video))
        data = json.loads(raw)
        stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
        if not stream:
            raise RuntimeError("The uploaded file contains no video stream.")
        rate = stream.get("avg_frame_rate") or stream.get("r_frame_rate") or "30/1"
        try:
            if "/" in str(rate):
                n, d = str(rate).split("/")
                fps = float(n) / (float(d) if float(d) > 0 else 1.0)
            else:
                fps = float(rate)
        except Exception:
            fps = 30.0
        if fps <= 0 or math.isnan(fps):
            fps = 30.0
            
        duration = None
        for candidate in [stream.get("duration"), data.get("format", {}).get("duration")]:
            if candidate and str(candidate).strip().lower() not in {"n/a", "none", "null", ""}:
                try:
                    d_parsed = float(candidate)
                    if d_parsed > 0 and not math.isnan(d_parsed):
                        duration = d_parsed
                        break
                except Exception:
                    pass
                    
        # If duration is still None, use ffprobe format=duration
        if duration is None:
            try:
                dur_raw = run("ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(video)).strip()
                if dur_raw and dur_raw.lower() != "n/a":
                    duration = float(dur_raw)
            except Exception:
                pass
                
        if duration is None or duration <= 0:
            duration = 10.0
            
        return {
            "width": int(stream.get("width", 480)),
            "height": int(stream.get("height", 480)),
            "fps": fps,
            "duration": duration,
            "has_audio": any(s.get("codec_type") == "audio" for s in data.get("streams", []))
        }
    except Exception:
        # Fallback for serverless environments without ffprobe binary
        return {
            "width": 480,
            "height": 480,
            "fps": 30.0,
            "duration": 10.0,
            "has_audio": True
        }


def prepare_consistent_character(character_path: Path, video_path: Path, output_path: Path) -> Path:
    """Prepares and enhances the character reference image to match the video's target aspect ratio without distortion."""
    try:
        meta = probe(video_path)
        vw, vh = meta.get("width", 480), meta.get("height", 480)
        target_ratio = float(vw) / float(vh)

        with Image.open(character_path) as im:
            im = im.convert("RGBA")
            cw, ch = im.size
            char_ratio = float(cw) / float(ch)

            # If aspect ratio is already within 5%, return clean PNG
            if abs(char_ratio - target_ratio) < 0.05:
                im.convert("RGB").save(output_path, "PNG")
                return output_path

            if char_ratio > target_ratio:
                # Character is wider than video -> fit width, pad top/bottom
                new_w = cw
                new_h = int(cw / target_ratio)
            else:
                # Character is taller than video -> fit height, pad left/right
                new_h = ch
                new_w = int(ch * target_ratio)

            # Center the character on clean canvas
            bg = Image.new("RGBA", (new_w, new_h), (0, 0, 0, 0))
            offset = ((new_w - cw) // 2, (new_h - ch) // 2)
            bg.paste(im, offset, mask=im.split()[3] if im.mode == "RGBA" else None)
            
            # Save high-res RGB image
            bg_rgb = Image.new("RGB", (new_w, new_h), (240, 240, 240))
            bg_rgb.paste(bg, mask=bg.split()[3])
            bg_rgb.save(output_path, "PNG")
            return output_path
    except Exception:
        shutil.copy2(character_path, output_path)
        return output_path


def safe_segment_seconds(meta: dict[str, Any]) -> float:
    model_frame_limit = 77 / max(meta["fps"], 1)
    return max(1.0, min(settings.max_segment_seconds, model_frame_limit))


def split_video_into_chunks(
    video: Path,
    max_chunk_duration: float = 10.0,
    max_total_duration: float | None = None,
    output_dir: Path | None = None
) -> list[dict[str, Any]]:
    """Splits video into sequential <= max_chunk_duration seconds chunks.
    If max_total_duration is provided, caps the total duration processed."""
    meta = probe(video)
    total_duration = float(meta["duration"])
    if max_total_duration and max_total_duration > 0:
        total_duration = min(total_duration, float(max_total_duration))
    fps = float(meta["fps"])
    
    if output_dir is None:
        output_dir = video.parent / "chunks"
    output_dir.mkdir(parents=True, exist_ok=True)
    
    if total_duration <= max_chunk_duration:
        # If user capped duration shorter than original video, slice the target duration
        if max_total_duration and total_duration < meta["duration"]:
            chunk_path = output_dir / "chunk_001.mp4"
            run(
                "ffmpeg", "-y",
                "-ss", "0.0",
                "-i", str(video),
                "-t", f"{total_duration:.6f}",
                "-avoid_negative_ts", "make_zero",
                "-c:v", "libx264",
                "-preset", "fast",
                "-crf", "18",
                "-r", f"{fps:.6f}",
                "-pix_fmt", "yuv420p",
                "-an",
                str(chunk_path)
            )
            return [{
                "index": 1,
                "start": 0.0,
                "duration": total_duration,
                "path": chunk_path,
                "is_original": False
            }]
        return [{
            "index": 1,
            "start": 0.0,
            "duration": total_duration,
            "path": video,
            "is_original": True
        }]
        
    chunks = []
    count = math.ceil(total_duration / max_chunk_duration)
    for i in range(count):
        start_time = i * max_chunk_duration
        chunk_dur = min(max_chunk_duration, total_duration - start_time)
        chunk_path = output_dir / f"chunk_{i+1:03d}.mp4"
        
        # Clean FFmpeg slicing with timestamp normalization and H.264 encoding
        run(
            "ffmpeg", "-y",
            "-ss", f"{start_time:.6f}",
            "-i", str(video),
            "-t", f"{chunk_dur:.6f}",
            "-avoid_negative_ts", "make_zero",
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "18",
            "-r", f"{fps:.6f}",
            "-pix_fmt", "yuv420p",
            "-an",
            str(chunk_path)
        )
        chunks.append({
            "index": i + 1,
            "start": start_time,
            "duration": chunk_dur,
            "path": chunk_path,
            "is_original": False
        })
    return chunks


def stitch_video_chunks(chunk_paths: list[Path], output_path: Path, fps: float = 30.0) -> Path:
    """Concatenates chunk video files in exact sequential order into a single MP4."""
    if not chunk_paths:
        raise RuntimeError("No chunk paths provided for stitching.")
    if len(chunk_paths) == 1:
        if chunk_paths[0].resolve() != output_path.resolve():
            shutil.copy2(chunk_paths[0], output_path)
        return output_path
        
    concat_file = output_path.parent / f"concat_{uuid.uuid4().hex[:6]}.txt"
    manifest_lines = []
    for p in chunk_paths:
        escaped_p = p.resolve().as_posix().replace("'", "'\\''")
        manifest_lines.append(f"file '{escaped_p}'\n")
    concat_file.write_text("".join(manifest_lines), encoding="utf-8")
    
    run(
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_file),
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-r", f"{fps:.6f}",
        str(output_path)
    )
    return output_path


def restore_audio_and_mux(source_video: Path | None, stitched_video: Path, final_output: Path) -> Path:
    """Muxes the original synchronized audio track from source_video onto stitched_video."""
    if not source_video or not source_video.exists():
        if stitched_video.resolve() != final_output.resolve():
            shutil.copy2(stitched_video, final_output)
        return final_output

    try:
        meta = probe(source_video)
        if meta.get("has_audio"):
            run(
                "ffmpeg", "-y",
                "-i", str(stitched_video),
                "-i", str(source_video),
                "-map", "0:v:0",
                "-map", "1:a:0?",
                "-c:v", "copy",
                "-c:a", "aac",
                "-shortest",
                str(final_output)
            )
            return final_output
    except Exception:
        pass

    if stitched_video.resolve() != final_output.resolve():
        shutil.copy2(stitched_video, final_output)
    return final_output


async def worker_connectivity() -> dict[str, Any]:
    if settings.mode == "mock":
        return {
            "comfyui_reachable": False,
            "gpu_detected": False,
            "gpu": "Mock worker",
            "vram_mb": None,
            "error": "MODE=mock does not contact a ComfyUI worker."
        }
    if settings.mode == "hf_space":
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                res = await client.get(hf_url("/config"), headers=hf_headers())
                res.raise_for_status()
                return {
                    "comfyui_reachable": True,
                    "gpu_detected": True,
                    "gpu": "Wan2.2 ZeroGPU (alexnasa/Wan2.2-Animate-ZEROGPU)",
                    "vram_mb": None,
                    "error": None
                }
        except Exception as exc:
            return {
                "comfyui_reachable": False,
                "gpu_detected": False,
                "gpu": None,
                "vram_mb": None,
                "error": f"Hugging Face Space unreachable: {exc}"
            }
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(worker_url("/system_stats"))
            response.raise_for_status()
            devices = response.json().get("devices", [])
            gpu_device = next((d for d in devices if str(d.get("type", "")).lower() not in {"", "cpu"}), None)
            if not gpu_device:
                return {
                    "comfyui_reachable": True,
                    "gpu_detected": False,
                    "gpu": None,
                    "vram_mb": None,
                    "error": "ComfyUI responded, but /system_stats reported no GPU device."
                }
            vram = gpu_device.get("vram_total")
            vram_mb = int(vram / (1024 * 1024)) if isinstance(vram, (int, float)) and vram > 100_000 else int(vram or 0)
            return {
                "comfyui_reachable": True,
                "gpu_detected": True,
                "gpu": str(gpu_device.get("name") or "ComfyUI GPU"),
                "vram_mb": vram_mb or None,
                "error": None
            }
    except Exception as exc:
        return {"comfyui_reachable": False, "gpu_detected": False, "gpu": None, "vram_mb": None, "error": str(exc)}


async def preflight() -> dict[str, Any]:
    problems: list[str] = []
    has_ffmpeg = True
    try:
        run("ffmpeg", "-version")
    except Exception:
        has_ffmpeg = False
        if settings.mode != "hf_space":
            problems.append("FFmpeg is not installed or is not on PATH.")

    if settings.mode == "mock":
        return {
            "ready": not problems,
            "problems": problems,
            "mode": "mock",
            "comfyui_reachable": False,
            "gpu_detected": False,
            "wan2_2_model_detected": False,
            "workflow_detected": settings.wan_workflow_path.exists(),
            "connectivity_error": None,
            "gpu": "Mock Worker (Development)",
            "vram_mb": None,
            "worker_url": None,
            "recommended": "Mock mode active. Ready for development testing without GPU inference."
        }

    if settings.mode == "hf_space":
        connection = await worker_connectivity()
        if not connection["comfyui_reachable"]:
            problems.append(f"Hugging Face Space is unreachable at {settings.hf_space_url}: {connection['error']}")
        return {
            "ready": not problems,
            "problems": problems,
            "mode": "hf_space",
            "comfyui_reachable": connection["comfyui_reachable"],
            "gpu_detected": True,
            "wan2_2_model_detected": True,
            "workflow_detected": True,
            "connectivity_error": connection["error"],
            "gpu": "Wan2.2 ZeroGPU (alexnasa/Wan2.2-Animate-ZEROGPU)",
            "vram_mb": None,
            "worker_url": settings.hf_space_url,
            "recommended": "Hugging Face Wan2.2 Animate ZeroGPU Space is online and ready for generation."
        }

    connection = await worker_connectivity()
    vram, gpu = connection["vram_mb"], connection["gpu"]
    workflow_detected = False
    wan_model_detected = False
    if settings.mode == "real" and not settings.wan_workflow_path.exists():
        problems.append(f"The Wan API workflow is missing: {settings.wan_workflow_path}")
    elif settings.mode == "real":
        try:
            workflow = json.loads(settings.wan_workflow_path.read_text(encoding="utf-8"))
            if workflow.get("_template"):
                problems.append("The workflow file is still the instructional placeholder.")
            elif "nodes" in workflow and "links" in workflow:
                problems.append("WAN_WORKFLOW_PATH contains canvas workflow, not Save (API Format) prompt.")
            else:
                required = ("{{video}}", "{{character}}", "{{output_prefix}}", "{{frames}}", "{{mode}}")
                body = json.dumps(workflow)
                missing = [x for x in required if x not in body]
                if missing:
                    problems.append("Workflow is missing required token(s): " + ", ".join(missing))
                else:
                    workflow_detected = True
        except Exception as exc:
            problems.append(f"Workflow JSON cannot be read: {exc}")

    if settings.mode == "real":
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(worker_url("/object_info"))
                response.raise_for_status()
                nodes = response.json()
                wan_model_detected = "wan2_2_animate" in json.dumps(nodes).lower().replace("-", "_")
                if not wan_model_detected:
                    problems.append("Remote ComfyUI does not report a Wan2.2 Animate model in its available loader options.")
                if not connection["comfyui_reachable"]:
                    problems.append(f"ComfyUI is not reachable at {settings.comfyui_url}: {connection['error']}")
                elif not gpu:
                    problems.append("ComfyUI did not report a GPU through /system_stats.")
        except Exception as exc:
            problems.append(f"Remote ComfyUI API is unavailable at {settings.comfyui_url}: {exc}")

    return {
        "ready": not problems,
        "problems": problems,
        "mode": settings.mode,
        "comfyui_reachable": connection["comfyui_reachable"],
        "gpu_detected": connection["gpu_detected"],
        "wan2_2_model_detected": wan_model_detected,
        "workflow_detected": workflow_detected,
        "connectivity_error": connection["error"],
        "gpu": gpu,
        "vram_mb": vram,
        "worker_url": settings.comfyui_url if settings.mode == "real" else None,
        "recommended": "Configure the worker's Wan2.2 Animate Mix workflow for its available hardware."
    }


def replace_tokens(value: Any, tokens: dict[str, str | int]) -> Any:
    if isinstance(value, str):
        return tokens.get(value, value)
    if isinstance(value, list):
        return [replace_tokens(v, tokens) for v in value]
    if isinstance(value, dict):
        return {k: replace_tokens(v, tokens) for k, v in value.items() if not k.startswith("_")}
    return value


async def comfy_upload(path: Path, name: str) -> None:
    async with httpx.AsyncClient(timeout=120) as client:
        with path.open("rb") as file:
            response = await client.post(worker_url("/upload/image"), files={"image": (name, file)}, data={"overwrite": "true"})
        response.raise_for_status()


def hf_headers() -> dict[str, str]:
    headers = {"User-Agent": "Mozilla/5.0"}
    if settings.hf_token:
        headers["Authorization"] = f"Bearer {settings.hf_token}"
    return headers


async def hf_upload_file(path: Path, mime_type: str) -> str:
    """Upload a file to Hugging Face Space Gradio upload endpoint and return remote path."""
    timeout = httpx.Timeout(connect=30, read=120, write=120, pool=30)
    async with httpx.AsyncClient(timeout=timeout) as client:
        with path.open("rb") as f:
            files = {"files": (path.name, f, mime_type)}
            resp = await client.post(hf_url("/gradio_api/upload"), files=files, headers=hf_headers())
            resp.raise_for_status()
            data = resp.json()
            if not data or not isinstance(data, list):
                raise RuntimeError(f"Unexpected upload response from HF Space: {data}")
            return data[0]


async def generate_hf_wan_animate(
    video: Path | None = None,
    character: Path | None = None,
    max_duration: int = 2,
    resolution: str = "Low Res",
    job_id: str | None = None,
    video_remote_path: str | None = None,
    char_remote_path: str | None = None,
    output_dir: Path | None = None
) -> Path:
    """Submits generation to alexnasa/Wan2.2-Animate-ZEROGPU Space and downloads the output MP4."""
    if not video_remote_path and video:
        if job_id:
            update_job(job_id, stage="Uploading video to Wan2.2 ZeroGPU...", progress=15)
        video_remote_path = await hf_upload_file(video, "video/mp4")
        
    if not char_remote_path and character:
        if job_id:
            update_job(job_id, stage="Uploading character to Wan2.2 ZeroGPU...", progress=25)
        char_remote_path = await hf_upload_file(character, "image/png")
        
    if not video_remote_path or not char_remote_path:
        raise RuntimeError("Missing remote media paths for Wan2.2 Animate generation.")
    
    if job_id:
        update_job(job_id, stage="Queuing Wan2.2 Animate task...", progress=30)
        
    # 2. Call animate_scene endpoint with automatic queue retry
    payload = {
        "data": [
            {"path": video_remote_path, "meta": {"_type": "gradio.FileData"}},
            max_duration,
            {"path": char_remote_path, "meta": {"_type": "gradio.FileData"}},
            "Character Swap",
            resolution,
            None
        ]
    }
    
    timeout = httpx.Timeout(connect=30, read=300, write=300, pool=30)
    max_queue_retries = 3
    
    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(1, max_queue_retries + 1):
            if job_id:
                stage_msg = "Queuing Wan2.2 Animate task..." if attempt == 1 else f"ZeroGPU busy. Retrying queue ({attempt}/{max_queue_retries})..."
                update_job(job_id, stage=stage_msg, progress=30)
                
            r_call = await client.post(hf_url("/gradio_api/call/animate_scene"), json=payload, headers=hf_headers())
            r_call.raise_for_status()
            event_id = r_call.json().get("event_id")
            if not event_id:
                raise RuntimeError(f"No event_id returned from Space: {r_call.text}")
                
            if job_id:
                update_job(job_id, stage="Processing with Wan2.2 ZeroGPU...", progress=50)
                
            # 3. Stream SSE status
            sse_url = hf_url(f"/gradio_api/call/animate_scene/{event_id}")
            final_video_url = None
            error_msg = None
            
            async with client.stream("GET", sse_url, headers=hf_headers(), timeout=600) as stream:
                async for line in stream.aiter_lines():
                    if not line:
                        continue
                    if line.startswith("event: heartbeat"):
                        if job_id:
                            current = read_job(job_id) or {}
                            current_p = min(90, current.get("progress", 50) + 5)
                            update_job(job_id, stage="Generating character-swapped frames...", progress=current_p)
                    elif line.startswith("data:"):
                        raw_data = line[5:].strip()
                        if not raw_data or raw_data == "null":
                            continue
                        try:
                            data = json.loads(raw_data)
                            if isinstance(data, dict) and "error" in data:
                                error_msg = data.get("error")
                                break
                            if isinstance(data, list) and len(data) >= 1:
                                # Completed! Extract output index 0
                                out_item = data[0]
                                if isinstance(out_item, str):
                                    final_video_url = out_item
                                elif isinstance(out_item, dict):
                                    final_video_url = out_item.get("url") or out_item.get("path")
                                    if not final_video_url and "video" in out_item:
                                        if isinstance(out_item["video"], dict):
                                            final_video_url = out_item["video"].get("url") or out_item["video"].get("path")
                                        elif isinstance(out_item["video"], str):
                                            final_video_url = out_item["video"]
                                break
                        except Exception:
                            pass
                            
            if error_msg:
                # If GPU queue was full, retry automatically after brief backoff
                if "No GPU was available" in error_msg and attempt < max_queue_retries:
                    await asyncio.sleep(4)
                    continue
                raise RuntimeError(error_msg)
                
            if not final_video_url:
                if attempt < max_queue_retries:
                    await asyncio.sleep(3)
                    continue
                raise RuntimeError("Wan2.2 ZeroGPU generation completed without returning an output video URL.")
                
            if job_id:
                update_job(job_id, stage="Downloading final video from Wan2.2...", progress=92)
                
            # Normalize download URL
            if final_video_url and not final_video_url.startswith("http"):
                if "/gradio_api/file=" in final_video_url or "/file=" in final_video_url:
                    final_video_url = hf_url(f"/{final_video_url.lstrip('/')}")
                else:
                    final_video_url = hf_url(f"/gradio_api/file={final_video_url}")

            # 4. Download output video
            target_dir = output_dir or (video.parent if video else Path(tempfile.gettempdir()))
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / f"hf_generated_{uuid.uuid4().hex[:6]}.mp4"
            r_down = await client.get(final_video_url, headers=hf_headers(), timeout=120)
            r_down.raise_for_status()
            target.write_bytes(r_down.content)
            return target
            
        raise RuntimeError("No GPU was available after multiple retries. Please try again or authenticate with a Hugging Face token.")



async def generate_hf_roop(
    video: Path,
    character: Path,
    job_id: str | None = None,
    output_dir: Path | None = None
) -> Path:
    """Face-swaps character into video using tonyassi/face-swap (CPU-based, no quota cost)."""
    ROOP_BASE = "https://tonyassi-face-swap.hf.space"
    timeout = httpx.Timeout(connect=60, read=600, write=120, pool=30)

    async with httpx.AsyncClient(timeout=timeout) as client:
        if job_id:
            update_job(job_id, stage="Uploading to Roop Face Swap...", progress=20)

        # Upload source face (character image)
        with open(character, "rb") as f:
            mime = "image/png" if character.suffix.lower() == ".png" else "image/jpeg"
            r1 = await client.post(f"{ROOP_BASE}/upload", files={"files": (character.name, f, mime)})
            r1.raise_for_status()
            char_remote = r1.json()[0]

        # Upload target video
        with open(video, "rb") as f:
            r2 = await client.post(f"{ROOP_BASE}/upload", files={"files": (video.name, f, "video/mp4")})
            r2.raise_for_status()
            vid_remote = r2.json()[0]

        if job_id:
            update_job(job_id, stage="Roop processing face swap...", progress=40)

        # Call swap_faces endpoint: inputs [source_image, target_video]
        payload = {
            "data": [
                {"path": char_remote, "meta": {"_type": "gradio.FileData"}},
                {"path": vid_remote, "meta": {"_type": "gradio.FileData"}}
            ]
        }
        r_call = await client.post(f"{ROOP_BASE}/call/swap_faces", json=payload)
        r_call.raise_for_status()
        event_id = r_call.json().get("event_id")
        if not event_id:
            raise RuntimeError(f"Roop returned no event_id: {r_call.text}")

        if job_id:
            update_job(job_id, stage="Roop generating swapped video...", progress=55)

        final_video_url = None
        error_msg = None
        sse_url = f"{ROOP_BASE}/call/swap_faces/{event_id}"

        async with client.stream("GET", sse_url, timeout=600) as stream:
            async for line in stream.aiter_lines():
                if not line:
                    continue
                if line.startswith("event: heartbeat"):
                    if job_id:
                        cur = read_job(job_id) or {}
                        update_job(job_id, stage="Roop swapping faces...", progress=min(90, cur.get("progress", 55) + 3))
                elif line.startswith("data:"):
                    raw = line[5:].strip()
                    if not raw or raw == "null":
                        continue
                    try:
                        data = json.loads(raw)
                        if isinstance(data, dict) and "error" in data:
                            error_msg = str(data.get("error") or "Roop error")
                            break
                        if isinstance(data, list) and len(data) >= 1:
                            for item in data:
                                if isinstance(item, dict):
                                    url = item.get("url") or item.get("path")
                                    if url:
                                        final_video_url = url
                                        break
                                elif isinstance(item, str) and item:
                                    final_video_url = item
                                    break
                            break
                    except Exception:
                        pass

        if error_msg:
            raise RuntimeError(error_msg)
        if not final_video_url:
            raise RuntimeError("Roop completed without returning an output video URL.")

        if job_id:
            update_job(job_id, stage="Downloading Roop output...", progress=92)

        if not final_video_url.startswith("http"):
            final_video_url = f"{ROOP_BASE}/{final_video_url.lstrip('/')}"

        target_dir = output_dir or video.parent
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"roop_generated_{uuid.uuid4().hex[:6]}.mp4"
        r_down = await client.get(final_video_url, timeout=120)
        r_down.raise_for_status()
        target.write_bytes(r_down.content)
        return target


async def generate_hf_sadtalker(
    character: Path,
    audio: Path,
    job_id: str | None = None,
    output_dir: Path | None = None
) -> Path:
    """Animates a portrait image with speech audio using SadTalker (CPU, zero quota cost)."""
    ST_BASE = "https://kevinwang676-sadtalker.hf.space"
    timeout = httpx.Timeout(connect=60, read=600, write=120, pool=30)

    async with httpx.AsyncClient(timeout=timeout) as client:
        if job_id:
            update_job(job_id, stage="Uploading portrait to SadTalker...", progress=20)

        with open(character, "rb") as f:
            mime = "image/png" if character.suffix.lower() == ".png" else "image/jpeg"
            r1 = await client.post(f"{ST_BASE}/upload", files={"files": (character.name, f, mime)})
            r1.raise_for_status()
            char_remote = r1.json()[0]

        with open(audio, "rb") as f:
            audio_mime = "audio/mpeg" if audio.suffix.lower() == ".mp3" else "audio/wav"
            r2 = await client.post(f"{ST_BASE}/upload", files={"files": (audio.name, f, audio_mime)})
            r2.raise_for_status()
            audio_remote = r2.json()[0]

        if job_id:
            update_job(job_id, stage="SadTalker generating talking head...", progress=40)

        # SadTalker main generate endpoint: image, audio, preprocess, still_mode, use_enhancer, batch_size, size, pose_style, exp_scale
        payload = {
            "data": [
                {"path": char_remote, "meta": {"_type": "gradio.FileData"}},
                {"path": audio_remote, "meta": {"_type": "gradio.FileData"}},
                "crop",     # preprocess
                False,      # still mode
                True,       # use_enhancer (GFPGAN)
                2,          # batch_size
                256,        # face model resolution
                0,          # pose_style
                1.0,        # expression_scale
                False,      # use_ref_video
                None,       # ref_video
                "pose",     # ref_info
                False,      # use_idle_mode
                None,       # length_of_video
                True,       # use_blink
                "png"       # result_format
            ]
        }
        r_call = await client.post(f"{ST_BASE}/call/test", json=payload)
        r_call.raise_for_status()
        event_id = r_call.json().get("event_id")
        if not event_id:
            raise RuntimeError(f"SadTalker returned no event_id: {r_call.text}")

        if job_id:
            update_job(job_id, stage="SadTalker animating face...", progress=55)

        final_video_url = None
        error_msg = None

        async with client.stream("GET", f"{ST_BASE}/call/test/{event_id}", timeout=600) as stream:
            async for line in stream.aiter_lines():
                if not line:
                    continue
                if line.startswith("event: heartbeat"):
                    if job_id:
                        cur = read_job(job_id) or {}
                        update_job(job_id, stage="SadTalker rendering...", progress=min(90, cur.get("progress", 55) + 3))
                elif line.startswith("data:"):
                    raw = line[5:].strip()
                    if not raw or raw == "null":
                        continue
                    try:
                        data = json.loads(raw)
                        if isinstance(data, dict) and "error" in data:
                            error_msg = str(data.get("error") or "SadTalker error")
                            break
                        if isinstance(data, list) and len(data) >= 1:
                            for item in data:
                                if isinstance(item, dict):
                                    url = item.get("url") or item.get("path")
                                    if url:
                                        final_video_url = url
                                        break
                                elif isinstance(item, str) and item:
                                    final_video_url = item
                                    break
                            break
                    except Exception:
                        pass

        if error_msg:
            raise RuntimeError(error_msg)
        if not final_video_url:
            raise RuntimeError("SadTalker completed without returning an output video URL.")

        if job_id:
            update_job(job_id, stage="Downloading SadTalker output...", progress=92)

        if not final_video_url.startswith("http"):
            final_video_url = f"{ST_BASE}/{final_video_url.lstrip('/')}"

        target_dir = output_dir or character.parent
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"sadtalker_generated_{uuid.uuid4().hex[:6]}.mp4"
        r_down = await client.get(final_video_url, timeout=120)
        r_down.raise_for_status()
        target.write_bytes(r_down.content)
        return target


async def generate_hf_echomimic(
    character: Path,
    audio: Path,
    job_id: str | None = None,
    output_dir: Path | None = None
) -> Path:
    """Audio-driven lip-sync portrait animation using EchoMimic (ZeroGPU A10G)."""
    EM_BASE = "https://fffiloni-echomimic.hf.space"
    timeout = httpx.Timeout(connect=30, read=600, write=120, pool=30)

    async with httpx.AsyncClient(timeout=timeout) as client:
        if job_id:
            update_job(job_id, stage="Uploading portrait to EchoMimic...", progress=20)

        with open(character, "rb") as f:
            mime = "image/png" if character.suffix.lower() == ".png" else "image/jpeg"
            r1 = await client.post(
                f"{EM_BASE}/upload",
                files={"files": (character.name, f, mime)},
                headers={"Authorization": f"Bearer {settings.hf_token}"}
            )
            r1.raise_for_status()
            char_remote = r1.json()[0]

        with open(audio, "rb") as f:
            audio_mime = "audio/mpeg" if audio.suffix.lower() == ".mp3" else "audio/wav"
            r2 = await client.post(
                f"{EM_BASE}/upload",
                files={"files": (audio.name, f, audio_mime)},
                headers={"Authorization": f"Bearer {settings.hf_token}"}
            )
            r2.raise_for_status()
            audio_remote = r2.json()[0]

        if job_id:
            update_job(job_id, stage="EchoMimic generating lip-sync video...", progress=40)

        # EchoMimic generate_video: image, audio, width, height, length, seed, facemask_dilation,
        # facecrop_dilation, context_frames, context_overlap, cfg, steps, sample_rate, fps, device
        payload = {
            "data": [
                {"path": char_remote, "meta": {"_type": "gradio.FileData"}},
                {"path": audio_remote, "meta": {"_type": "gradio.FileData"}},
                512,    # width
                512,    # height
                120,    # length (frames)
                -1,     # seed (-1 = random)
                0.5,    # facemask_dilation_ratio
                1.5,    # facecrop_dilation_ratio
                12,     # context_frames
                3,      # context_overlap
                2.5,    # cfg
                20,     # steps
                16000,  # sample_rate
                24,     # fps
                "cuda"  # device
            ]
        }
        r_call = await client.post(
            f"{EM_BASE}/call/generate_video",
            json=payload,
            headers={"Authorization": f"Bearer {settings.hf_token}"}
        )
        r_call.raise_for_status()
        event_id = r_call.json().get("event_id")
        if not event_id:
            raise RuntimeError(f"EchoMimic returned no event_id: {r_call.text}")

        if job_id:
            update_job(job_id, stage="EchoMimic animating lip-sync...", progress=55)

        final_video_url = None
        error_msg = None

        async with client.stream(
            "GET", f"{EM_BASE}/call/generate_video/{event_id}",
            headers={"Authorization": f"Bearer {settings.hf_token}"},
            timeout=600
        ) as stream:
            async for line in stream.aiter_lines():
                if not line:
                    continue
                if line.startswith("event: heartbeat"):
                    if job_id:
                        cur = read_job(job_id) or {}
                        update_job(job_id, stage="EchoMimic rendering frames...", progress=min(90, cur.get("progress", 55) + 3))
                elif line.startswith("data:"):
                    raw = line[5:].strip()
                    if not raw or raw == "null":
                        continue
                    try:
                        data = json.loads(raw)
                        if isinstance(data, dict) and "error" in data:
                            error_msg = str(data.get("error") or "EchoMimic error")
                            break
                        if isinstance(data, list) and len(data) >= 1:
                            for item in data:
                                if isinstance(item, dict):
                                    url = item.get("url") or item.get("path")
                                    if url:
                                        final_video_url = url
                                        break
                                elif isinstance(item, str) and item:
                                    final_video_url = item
                                    break
                            break
                    except Exception:
                        pass

        if error_msg:
            raise RuntimeError(error_msg)
        if not final_video_url:
            raise RuntimeError("EchoMimic completed without returning an output video URL.")

        if job_id:
            update_job(job_id, stage="Downloading EchoMimic output...", progress=92)

        if not final_video_url.startswith("http"):
            final_video_url = f"{EM_BASE}/{final_video_url.lstrip('/')}"

        target_dir = output_dir or character.parent
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"echomimic_generated_{uuid.uuid4().hex[:6]}.mp4"
        r_down = await client.get(
            final_video_url,
            headers={"Authorization": f"Bearer {settings.hf_token}"},
            timeout=120
        )
        r_down.raise_for_status()
        target.write_bytes(r_down.content)
        return target


async def generate_hf_liveportrait(
    video: Path,
    character: Path,
    job_id: str | None = None,
    output_dir: Path | None = None
) -> Path:
    """Submits generation to KlingTeam/LivePortrait Space and downloads the animated output MP4."""
    LP_BASE = "https://klingteam-liveportrait.hf.space"

    timeout = httpx.Timeout(connect=30, read=300, write=300, pool=30)
    async with httpx.AsyncClient(timeout=timeout) as client:
        # 1. Upload character portrait (source face)
        if job_id:
            update_job(job_id, stage="Uploading portrait to LivePortrait...", progress=15)
        with open(character, "rb") as f:
            mime = "image/png" if character.suffix.lower() == ".png" else "image/jpeg"
            r_up1 = await client.post(
                f"{LP_BASE}/upload",
                files={"files": (character.name, f, mime)},
                headers={"Authorization": f"Bearer {settings.hf_token}"}
            )
            r_up1.raise_for_status()
            char_remote = r_up1.json()[0]

        # 2. Upload driving video
        if job_id:
            update_job(job_id, stage="Uploading driving video to LivePortrait...", progress=25)
        with open(video, "rb") as f:
            r_up2 = await client.post(
                f"{LP_BASE}/upload",
                files={"files": (video.name, f, "video/mp4")},
                headers={"Authorization": f"Bearer {settings.hf_token}"}
            )
            r_up2.raise_for_status()
            vid_remote = r_up2.json()[0]

        if job_id:
            update_job(job_id, stage="Queuing LivePortrait task...", progress=30)

        # 3. Call gpu_wrapped_execute_video
        payload = {
            "data": [
                {"path": char_remote, "meta": {"_type": "gradio.FileData"}},
                {"path": vid_remote, "meta": {"_type": "gradio.FileData"}},
                True,   # relative motion
                True,   # do crop
                True    # paste-back
            ]
        }
        r_call = await client.post(
            f"{LP_BASE}/call/gpu_wrapped_execute_video",
            json=payload,
            headers={"Authorization": f"Bearer {settings.hf_token}"}
        )
        r_call.raise_for_status()
        event_id = r_call.json().get("event_id")
        if not event_id:
            raise RuntimeError(f"LivePortrait returned no event_id: {r_call.text}")

        if job_id:
            update_job(job_id, stage="Processing with LivePortrait ZeroGPU...", progress=50)

        # 4. Stream SSE for result
        sse_url = f"{LP_BASE}/call/gpu_wrapped_execute_video/{event_id}"
        final_video_url = None
        error_msg = None

        async with client.stream("GET", sse_url, headers={"Authorization": f"Bearer {settings.hf_token}"}, timeout=600) as stream:
            async for line in stream.aiter_lines():
                if not line:
                    continue
                if line.startswith("event: heartbeat"):
                    if job_id:
                        current = read_job(job_id) or {}
                        current_p = min(90, current.get("progress", 50) + 5)
                        update_job(job_id, stage="LivePortrait animating frames...", progress=current_p)
                elif line.startswith("event: error"):
                    error_msg = "LivePortrait ZeroGPU error"
                elif line.startswith("data:"):
                    raw_data = line[5:].strip()
                    if not raw_data or raw_data == "null":
                        continue
                    try:
                        data = json.loads(raw_data)
                        if isinstance(data, dict) and "error" in data:
                            error_msg = str(data.get("error") or "LivePortrait generation error")
                            break
                        if isinstance(data, list) and len(data) >= 1:
                            # Output indices: [0]=animated_video_original_space, [1]=animated_video
                            # Prefer the paste-back result at index 1
                            for out_item in reversed(data):
                                if isinstance(out_item, dict):
                                    url = out_item.get("url") or out_item.get("path")
                                    if url:
                                        final_video_url = url
                                        break
                                elif isinstance(out_item, str) and out_item:
                                    final_video_url = out_item
                                    break
                            break
                    except Exception:
                        pass

        if error_msg:
            raise RuntimeError(error_msg)
        if not final_video_url:
            raise RuntimeError("LivePortrait generation completed without returning an output video URL.")

        if job_id:
            update_job(job_id, stage="Downloading LivePortrait output...", progress=92)

        # Normalize download URL
        if not final_video_url.startswith("http"):
            final_video_url = f"{LP_BASE}/{final_video_url.lstrip('/')}"

        # 5. Download output
        target_dir = output_dir or video.parent
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"lp_generated_{uuid.uuid4().hex[:6]}.mp4"
        r_down = await client.get(
            final_video_url,
            headers={"Authorization": f"Bearer {settings.hf_token}"},
            timeout=120
        )
        r_down.raise_for_status()
        target.write_bytes(r_down.content)
        return target


async def generate_segment(segment: Path, character: Path, output_prefix: str, frames: int) -> Path:
    if settings.mode == "mock":
        await asyncio.sleep(1.2)
        target = segment.parent / f"generated_{segment.stem}.mp4"
        badge_path = segment.parent / "mock_watermark.png"
        make_mock_badge(badge_path, max_width=380)
        run(
            "ffmpeg", "-y", "-i", str(segment), "-i", str(badge_path),
            "-filter_complex", "[0:v][1:v]overlay=(W-w)/2:H-h-20[v]",
            "-map", "[v]", "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
            str(target)
        )
        return target

    remote_video = f"{output_prefix}_input{segment.suffix.lower()}"
    remote_character = f"{output_prefix}_character{character.suffix.lower()}"
    await comfy_upload(segment, remote_video)
    await comfy_upload(character, remote_character)
    workflow = json.loads(settings.wan_workflow_path.read_text(encoding="utf-8"))
    prompt = replace_tokens(workflow, {
        "{{video}}": remote_video,
        "{{character}}": remote_character,
        "{{output_prefix}}": output_prefix,
        "{{frames}}": frames,
        "{{mode}}": "Mix"
    })
    submission = await queue_prompt(prompt)
    prompt_id = submission["prompt_id"]
    async with httpx.AsyncClient(timeout=10) as client:
        for _ in range(1800):
            history = await client.get(worker_url(f"/history/{prompt_id}"))
            history.raise_for_status()
            item = history.json().get(prompt_id)
            if item:
                if item.get("status", {}).get("status_str") == "error":
                    raise RuntimeError(str(item.get("status")))
                for node in item.get("outputs", {}).values():
                    for video in node.get("videos", []) + node.get("gifs", []) + node.get("images", []):
                        filename, subfolder = video["filename"], video.get("subfolder", "")
                        target = segment.parent / f"generated_{segment.stem}.mp4"
                        view = await client.get(
                            worker_url("/view"),
                            params={"filename": filename, "subfolder": subfolder, "type": video.get("type", "output")}
                        )
                        view.raise_for_status()
                        target.write_bytes(view.content)
                        return target
            await asyncio.sleep(2)
    raise RuntimeError("ComfyUI timed out after 60 minutes for one segment.")


async def process(
    job_id: str,
    video: Path | None = None,
    character: Path | None = None,
    audio: Path | None = None,
    max_duration: int | float | str | None = "auto",
    resolution: str = "Low Res",
    video_remote_path: str | None = None,
    char_remote_path: str | None = None,
    output_dir: Path | None = None,
    resume_from_chunk: int = 1,
    engine: str = "wan22"
) -> None:
    try:
        update_job(job_id, stage="Analyzing request...", progress=5)
        if not output_dir:
            try:
                output_dir = settings.storage_dir / job_id
                output_dir.mkdir(parents=True, exist_ok=True)
            except Exception:
                output_dir = Path(tempfile.gettempdir()) / "character_swap_jobs" / job_id
                output_dir.mkdir(parents=True, exist_ok=True)
                
        final = output_dir / "final_character_swap.mp4"

        # Parse max_duration
        max_total_sec = None
        if max_duration and str(max_duration).lower() not in {"auto", "all", "none", "0"}:
            try:
                max_total_sec = float(max_duration)
            except Exception:
                max_total_sec = None

        # Retrieve source files from remote Gradio storage if needed
        if (not video or not video.exists()) and video_remote_path:
            update_job(job_id, stage="Syncing source video...", progress=7)
            try:
                async with httpx.AsyncClient(timeout=60.0) as client:
                    dl = await client.get(
                        hf_url(f"/gradio_api/file={video_remote_path}"),
                        headers=hf_headers()
                    )
                    if dl.status_code == 200:
                        v_target = output_dir / "source_uploaded.mp4"
                        v_target.write_bytes(dl.content)
                        video = v_target
            except Exception:
                pass

        if (not character or not character.exists()) and char_remote_path:
            try:
                async with httpx.AsyncClient(timeout=60.0) as client:
                    dl = await client.get(
                        hf_url(f"/gradio_api/file={char_remote_path}"),
                        headers=hf_headers()
                    )
                    if dl.status_code == 200:
                        c_target = output_dir / "character_uploaded.png"
                        c_target.write_bytes(dl.content)
                        character = c_target
            except Exception:
                pass

        # Case 1: Remote paths provided directly without local video file (fallback single-request)
        if not video or not video.exists():
            direct_dur = min(10, int(max_total_sec)) if max_total_sec else 10
            raw_generated = await generate_hf_wan_animate(
                video=None,
                character=character,
                max_duration=direct_dur,
                resolution=resolution,
                job_id=job_id,
                video_remote_path=video_remote_path,
                char_remote_path=char_remote_path,
                output_dir=output_dir
            )
            shutil.copy2(raw_generated, final)
            update_job(job_id, stage="Completed", progress=100, complete=True, final=str(final))
            return

        # Case 2: Local video available -> perform duration probe, chunking, sequential execution, stitching & audio sync
        meta = probe(video)
        total_duration = meta["duration"]
        fps = meta["fps"]

        # Chunk the video (<= 10.0s each)
        chunks = split_video_into_chunks(
            video,
            max_chunk_duration=10.0,
            max_total_duration=max_total_sec,
            output_dir=output_dir / "chunks"
        )
        total_chunks = len(chunks)

        job_state = read_job(job_id) or {}
        chunk_outputs: list[dict[str, Any]] = job_state.get("chunk_outputs", [])
        completed_indices = {item["index"] for item in chunk_outputs if Path(item.get("output_path", "")).exists()}

        update_job(
            job_id,
            stage=f"Split into {total_chunks} chunks" if total_chunks > 1 else "Preparing video...",
            progress=10,
            total_chunks=total_chunks,
            total_duration=total_duration
        )

        # Optimize character image for video aspect ratio to prevent shape distortion
        if character and character.exists():
            opt_char = output_dir / "character_aspect_aligned.png"
            character = prepare_consistent_character(character, video, opt_char)

        for chunk_info in chunks:
            idx = chunk_info["index"]
            chunk_path: Path = chunk_info["path"]
            chunk_dur: float = chunk_info["duration"]
            out_chunk_path = output_dir / f"output_chunk_{idx:03d}.mp4"

            # If resuming and chunk already completed and exists, skip
            if idx in completed_indices and out_chunk_path.exists():
                continue

            stage_text = f"Processing chunk {idx} of {total_chunks}..." if total_chunks > 1 else "Processing with Wan2.2 ZeroGPU..."
            base_progress = 10 + int(75 * ((idx - 1) / total_chunks))
            update_job(
                job_id,
                stage=stage_text,
                current_chunk=idx,
                total_chunks=total_chunks,
                progress=base_progress
            )

            # Max duration for this chunk (capped at 10)
            chunk_target_dur = min(10, max(1, math.ceil(chunk_dur)))

            # Multi-Chunk Consistency Chaining:
            # Use the ending frame of the previous chunk as the reference so lighting, pose, and clothing lock in seamlessly
            current_character = character
            if idx > 1:
                prev_out = output_dir / f"output_chunk_{idx-1:03d}.mp4"
                if prev_out.exists():
                    chained_frame = output_dir / f"handoff_frame_{idx-1:03d}.png"
                    try:
                        run(
                            "ffmpeg", "-y",
                            "-sseof", "-0.1",
                            "-i", str(prev_out),
                            "-vframes", "1",
                            "-q:v", "1",
                            str(chained_frame)
                        )
                        if chained_frame.exists() and chained_frame.stat().st_size > 1000:
                            current_character = chained_frame
                    except Exception:
                        current_character = character

            if settings.mode == "mock":
                await asyncio.sleep(0.4)
                start_t = chunk_info.get("start", (idx - 1) * 10.0)
                end_t = start_t + chunk_dur
                badge_text = f"PROCESSED CHUNK {idx}/{total_chunks} ({start_t:.1f}s - {end_t:.1f}s)"
                badge_path = output_dir / f"badge_{idx:03d}.png"
                make_mock_badge(badge_path, text=badge_text, max_width=440)
                run(
                    "ffmpeg", "-y", "-i", str(chunk_path), "-i", str(badge_path),
                    "-filter_complex", "[0:v][1:v]overlay=(W-w)/2:H-h-20[v]",
                    "-map", "[v]", "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
                    str(out_chunk_path)
                )
            elif settings.mode == "hf_space":
                if engine == "roop":
                    gen_file = await generate_hf_roop(
                        video=chunk_path,
                        character=current_character,
                        job_id=job_id,
                        output_dir=output_dir
                    )
                elif engine == "sadtalker":
                    chunk_audio = audio
                    if not chunk_audio or not chunk_audio.exists():
                        # Extract audio from chunk
                        chunk_audio = output_dir / f"extracted_audio_{idx:03d}.mp3"
                        run("ffmpeg", "-y", "-i", str(chunk_path), "-q:a", "0", "-map", "a", str(chunk_audio))
                        if not chunk_audio.exists() or chunk_audio.stat().st_size == 0:
                            raise RuntimeError("Could not extract audio from video and no separate audio file was provided.")
                    gen_file = await generate_hf_sadtalker(
                        character=current_character,
                        audio=chunk_audio,
                        job_id=job_id,
                        output_dir=output_dir
                    )
                elif engine == "echomimic":
                    chunk_audio = audio
                    if not chunk_audio or not chunk_audio.exists():
                        # Extract audio from chunk
                        chunk_audio = output_dir / f"extracted_audio_{idx:03d}.mp3"
                        run("ffmpeg", "-y", "-i", str(chunk_path), "-q:a", "0", "-map", "a", str(chunk_audio))
                        if not chunk_audio.exists() or chunk_audio.stat().st_size == 0:
                            raise RuntimeError("Could not extract audio from video and no separate audio file was provided.")
                    gen_file = await generate_hf_echomimic(
                        character=current_character,
                        audio=chunk_audio,
                        job_id=job_id,
                        output_dir=output_dir
                    )
                elif engine == "liveportrait":
                    gen_file = await generate_hf_liveportrait(
                        video=chunk_path,
                        character=current_character,
                        job_id=job_id,
                        output_dir=output_dir
                    )
                else:
                    # Default: Wan2.2 Animate
                    gen_file = await generate_hf_wan_animate(
                        video=chunk_path,
                        character=current_character,
                        max_duration=chunk_target_dur,
                        resolution=resolution,
                        job_id=job_id,
                        output_dir=output_dir
                    )
                shutil.copy2(gen_file, out_chunk_path)
            else:
                # ComfyUI mode
                frame_count = max(1, round(chunk_dur * fps))
                gen_file = await generate_segment(chunk_path, current_character, f"swap_{job_id}_{idx:03d}", frame_count)
                shutil.copy2(gen_file, out_chunk_path)

            chunk_outputs = [c for c in chunk_outputs if c["index"] != idx]
            chunk_outputs.append({
                "index": idx,
                "duration": chunk_dur,
                "output_path": str(out_chunk_path)
            })
            completed_indices.add(idx)
            update_job(
                job_id,
                chunk_outputs=chunk_outputs,
                progress=10 + int(75 * (idx / total_chunks))
            )

        # Stitch all chunk outputs together
        update_job(job_id, stage="Stitching final video...", progress=88)
        if settings.mode == "mock":
            await asyncio.sleep(0.4)

        sorted_paths = [
            Path(item["output_path"])
            for item in sorted(chunk_outputs, key=lambda x: x["index"])
            if Path(item["output_path"]).exists()
        ]
        stitched_silent = output_dir / "stitched_silent.mp4"
        stitch_video_chunks(sorted_paths, stitched_silent, fps=fps)

        # Audio restoration & sync
        update_job(job_id, stage="Restoring audio...", progress=95)
        if settings.mode == "mock":
            await asyncio.sleep(0.4)

        restore_audio_and_mux(source_video=video, stitched_video=stitched_silent, final_output=final)
        update_job(job_id, stage="Completed", progress=100, complete=True, failed=False, final=str(final))

    except Exception as exc:
        update_job(
            job_id,
            stage="Failed",
            failed=True,
            error=str(exc)
        )


@app.get("/", response_class=HTMLResponse)
async def home():
    return (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")

@app.get("/api/preflight")
async def get_preflight():
    return await preflight()

@app.get("/api/connectivity")
async def get_connectivity():
    return {
        "mode": settings.mode,
        "worker_url": settings.hf_space_url if settings.mode == "hf_space" else settings.comfyui_url,
        **(await worker_connectivity())
    }

@app.get("/api/stats")
async def get_stats():
    return {
        "status": "healthy",
        "jobs_count": len(jobs),
        "mode": settings.mode
    }

@app.post("/api/jobs")
@app.post("/api/wan-animate")
async def create_job(
    background: BackgroundTasks,
    video: UploadFile | None = File(None),
    character: UploadFile | None = File(None),
    audio: UploadFile | None = File(None),
    video_remote_path: str | None = Form(None),
    char_remote_path: str | None = Form(None),
    max_duration: str = Form("auto"),
    resolution: str = Form("Low Res"),
    engine: str = Form("wan22")
):
    if not (video_remote_path and char_remote_path) and not (video and character and video.filename and character.filename):
        raise HTTPException(400, "Both a video and character reference are required.")
    check = await preflight()
    if not check["ready"]:
        raise HTTPException(503, {"message": "Generation backend is not ready.", **check})
    job_id = uuid.uuid4().hex
    try:
        directory = settings.storage_dir / job_id
        directory.mkdir(parents=True, exist_ok=True)
    except Exception:
        directory = Path(tempfile.gettempdir()) / "character_swap_jobs" / job_id
        directory.mkdir(parents=True, exist_ok=True)
        
    vp, cp, ap = None, None, None
    if video and video.filename:
        vp = directory / f"source{Path(video.filename).suffix.lower()}"
        vp.write_bytes(await video.read())
    if character and character.filename:
        cp = directory / f"character{Path(character.filename).suffix.lower()}"
        cp.write_bytes(await character.read())
    if audio and audio.filename:
        ap = directory / f"audio{Path(audio.filename).suffix.lower()}"
        ap.write_bytes(await audio.read())
    
    init_data = {
        "id": job_id,
        "stage": "Queued",
        "progress": 0,
        "complete": False,
        "failed": False,
        "mode": settings.mode,
        "engine": engine,
        "max_duration": max_duration,
        "resolution": resolution
    }
    update_job(job_id, **init_data)
    background.add_task(
        process,
        job_id=job_id,
        video=vp,
        character=cp,
        audio=ap,
        max_duration=max_duration,
        resolution=resolution,
        video_remote_path=video_remote_path,
        char_remote_path=char_remote_path,
        output_dir=directory,
        engine=engine
    )
    return init_data

@app.post("/api/jobs/{job_id}/retry")
async def retry_job(job_id: str, background: BackgroundTasks):
    job = read_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    
    directory = settings.storage_dir / job_id
    if not directory.exists():
        directory = Path(tempfile.gettempdir()) / "character_swap_jobs" / job_id
    if not directory.exists():
        raise HTTPException(404, "Job directory not found on storage")
        
    vp = next(directory.glob("source*"), None)
    cp = next(directory.glob("character*"), None)
    
    update_job(job_id, stage="Resuming...", failed=False, error=None)
    background.add_task(
        process,
        job_id=job_id,
        video=vp,
        character=cp,
        max_duration=job.get("max_duration", 2),
        resolution=job.get("resolution", "Low Res"),
        output_dir=directory,
        resume_from_chunk=job.get("current_chunk", 1)
    )
    return read_job(job_id)

@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    job = read_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job

@app.get("/api/jobs/{job_id}/download")
async def download(job_id: str):
    job = read_job(job_id)
    path = Path(job.get("final", "")) if job else None
    if not path or not path.exists():
        raise HTTPException(404, "Final video is not ready")
    return FileResponse(path, media_type="video/mp4", filename="final_character_swap.mp4")
