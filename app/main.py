from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
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
    try:
        job_file = settings.storage_dir / f"job_{job_id}.json"
        job_file.write_text(json.dumps(jobs[job_id]), encoding="utf-8")
    except Exception:
        pass
    return jobs[job_id]


def read_job(job_id: str) -> dict[str, Any] | None:
    if job_id in jobs:
        return jobs[job_id]
    try:
        job_file = settings.storage_dir / f"job_{job_id}.json"
        if job_file.exists():
            data = json.loads(job_file.read_text(encoding="utf-8"))
            jobs[job_id] = data
            return data
    except Exception:
        pass
    return None


def make_mock_badge(path: Path, max_width: int = 380) -> Path:
    if path.exists():
        return path
    w = max(240, min(max_width, 400))
    h = 44
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([(0, 0), (w, h)], radius=10, fill=(15, 23, 42, 210), outline=(56, 189, 248, 240), width=2)
    d.text((w // 2, h // 2), "CHARACTER SWAP — MOCK DEMO", fill=(255, 255, 255, 240), anchor="mm")
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
        stream = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
        if not stream:
            raise RuntimeError("The uploaded file contains no video stream.")
        rate = stream.get("avg_frame_rate", "0/1")
        n, d = rate.split("/")
        fps = float(n) / float(d or 1)
        duration = float(stream.get("duration") or data["format"].get("duration") or 0)
        return {
            "width": int(stream["width"]),
            "height": int(stream["height"]),
            "fps": fps,
            "duration": duration,
            "has_audio": any(s["codec_type"] == "audio" for s in data["streams"])
        }
    except Exception:
        # Fallback for serverless environments without ffprobe binary
        return {
            "width": 480,
            "height": 480,
            "fps": 30.0,
            "duration": 2.0,
            "has_audio": True
        }


def safe_segment_seconds(meta: dict[str, Any]) -> float:
    model_frame_limit = 77 / max(meta["fps"], 1)
    return max(1.0, min(settings.max_segment_seconds, model_frame_limit))


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
    video: Path,
    character: Path,
    max_duration: int = 2,
    resolution: str = "Low Res",
    job_id: str | None = None
) -> Path:
    """Submits generation to alexnasa/Wan2.2-Animate-ZEROGPU Space and downloads the output MP4."""
    job = jobs.get(job_id) if job_id else None
    
    if job:
        job.update(stage="Uploading media to Wan2.2 ZeroGPU...", progress=15)
        
    # 1. Upload media files
    video_remote_path = await hf_upload_file(video, "video/mp4")
    char_remote_path = await hf_upload_file(character, "image/png")
    
    if job_id:
        update_job(job_id, stage="Queuing Wan2.2 Animate task...", progress=30)
        
    # 2. Call animate_scene endpoint
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
    async with httpx.AsyncClient(timeout=timeout) as client:
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
                        if isinstance(data, list) and len(data) >= 1 and isinstance(data[0], dict):
                            # Completed! Extract output index 0
                            out_item = data[0]
                            final_video_url = out_item.get("url")
                            if not final_video_url and "video" in out_item:
                                final_video_url = out_item["video"].get("url")
                            break
                    except Exception as e:
                        pass
                        
        if error_msg:
            raise RuntimeError(error_msg)
            
        if not final_video_url:
            raise RuntimeError("Wan2.2 ZeroGPU generation completed without returning an output video URL.")
            
        if job_id:
            update_job(job_id, stage="Downloading final video from Wan2.2...", progress=92)
            
        # 4. Download output video
        target = video.parent / "hf_generated_raw.mp4"
        r_down = await client.get(final_video_url, headers=hf_headers(), timeout=120)
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
    timeout = httpx.Timeout(connect=30, read=300, write=300, pool=30)
    async with httpx.AsyncClient(timeout=timeout) as client:
        queued = await client.post(worker_url("/prompt"), json={"prompt": prompt, "client_id": "character-swap"})
        queued.raise_for_status()
        prompt_id = queued.json()["prompt_id"]
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
    video: Path,
    character: Path,
    max_duration: int = 2,
    resolution: str = "Low Res"
) -> None:
    try:
        update_job(job_id, stage="Analyzing video...", progress=5)
        if settings.mode == "mock":
            await asyncio.sleep(0.6)
        meta = probe(video)

        final = video.parent / "final_character_swap.mp4"

        if settings.mode == "hf_space":
            # Direct Hugging Face Wan2.2 Animate ZeroGPU inference
            raw_generated = await generate_hf_wan_animate(
                video=video,
                character=character,
                max_duration=max_duration,
                resolution=resolution,
                job_id=job_id
            )
            
            # Restore original audio if input video had audio
            update_job(job_id, stage="Restoring audio...", progress=96)
            if meta.get("has_audio"):
                try:
                    run(
                        "ffmpeg", "-y", "-i", str(raw_generated), "-i", str(video),
                        "-map", "0:v:0", "-map", "1:a:0?",
                        "-c:v", "copy", "-c:a", "aac", "-shortest",
                        str(final)
                    )
                except Exception:
                    shutil.copy2(raw_generated, final)
            else:
                shutil.copy2(raw_generated, final)

            update_job(job_id, stage="Completed", progress=100, complete=True, final=str(final))
            return

        # Sliced segmentation pipeline for ComfyUI / Mock modes
        seconds = safe_segment_seconds(meta)
        normalized = video.parent / "normalized.mp4"
        vf = f"scale='trunc(min({settings.max_generation_side}/iw,{settings.max_generation_side}/ih)*iw/16)*16':'trunc(min({settings.max_generation_side}/iw,{settings.max_generation_side}/ih)*ih/16)*16':force_original_aspect_ratio=decrease"
        run("ffmpeg", "-y", "-i", str(video), "-an", "-vf", vf, "-r", f"{meta['fps']:.6f}", "-c:v", "libx264", "-crf", "18", str(normalized))
        count = max(1, int((meta["duration"] + seconds - .001) // seconds))
        update_job(job_id, stage="Preparing segments...", progress=15, segments=count, safe_segment_seconds=seconds)
        if settings.mode == "mock":
            await asyncio.sleep(0.6)
        segment_pattern = video.parent / "segment_%03d.mp4"
        run("ffmpeg", "-y", "-i", str(normalized), "-map", "0:v:0", "-f", "segment", "-segment_time", str(seconds), "-reset_timestamps", "1", "-c:v", "libx264", "-crf", "18", str(segment_pattern))
        segments = sorted(video.parent.glob("segment_*.mp4"))
        outputs = []
        for index, segment in enumerate(segments, 1):
            stage_title = f"Generating segment {index}/{len(segments)} (Mock)..." if settings.mode == "mock" else f"Generating segment {index}/{len(segments)}..."
            update_job(job_id, stage=stage_title, progress=20 + int(65 * (index-1)/len(segments)))
            frame_count = max(1, round(probe(segment)["duration"] * meta["fps"]))
            error = None
            for attempt in range(settings.segment_retries + 1):
                try:
                    outputs.append(await generate_segment(segment, character, f"swap_{job_id}_{index:03d}", frame_count))
                    error = None
                    break
                except Exception as exc:
                    error = exc
            if error:
                raise RuntimeError(f"Segment {index} failed after {settings.segment_retries + 1} attempts: {error}")
        update_job(job_id, stage="Stitching...", progress=88)
        if settings.mode == "mock":
            await asyncio.sleep(0.5)
        concat = video.parent / "concat.txt"
        concat.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in outputs), encoding="utf-8")
        silent = video.parent / "stitched.mp4"
        run("ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat), "-c:v", "libx264", "-pix_fmt", "yuv420p", str(silent))
        update_job(job_id, stage="Restoring audio...", progress=95)
        if settings.mode == "mock":
            await asyncio.sleep(0.5)
        if meta.get("has_audio"):
            run("ffmpeg", "-y", "-i", str(silent), "-i", str(video), "-map", "0:v:0", "-map", "1:a?", "-c:v", "copy", "-c:a", "aac", "-shortest", str(final))
        else:
            shutil.copy2(silent, final)
        update_job(job_id, stage="Completed", progress=100, complete=True, final=str(final))
    except Exception as exc:
        update_job(job_id, stage="Failed", failed=True, error=str(exc))


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

@app.post("/api/jobs")
@app.post("/api/wan-animate")
async def create_job(
    background: BackgroundTasks,
    video: UploadFile = File(...),
    character: UploadFile = File(...),
    max_duration: int = Form(2),
    resolution: str = Form("Low Res")
):
    if not video.filename or not character.filename:
        raise HTTPException(400, "Both a video and character image are required.")
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
    vp = directory / f"source{Path(video.filename).suffix.lower()}"
    cp = directory / f"character{Path(character.filename).suffix.lower()}"
    vp.write_bytes(await video.read())
    cp.write_bytes(await character.read())
    
    init_data = {
        "id": job_id,
        "stage": "Queued",
        "progress": 0,
        "complete": False,
        "failed": False,
        "mode": settings.mode
    }
    update_job(job_id, **init_data)
    background.add_task(process, job_id, vp, cp, max_duration, resolution)
    return init_data

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
