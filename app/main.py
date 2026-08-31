import asyncio
import json
import math
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

async def upload_to_cdn(filepath: Path) -> str:
    import httpx, re
    async with httpx.AsyncClient(timeout=120.0) as client:
        with open(filepath, "rb") as f:
            resp = await client.post("https://tmpfiles.org/api/v1/upload", files={"file": f})
            resp.raise_for_status()
            url = resp.json()["data"]["url"]
            
        html_resp = await client.get(url)
        match = re.search(r'href="(https://tmpfiles\.org/dl/.*?)"', html_resp.text)
        if match:
            return match.group(1)
        raise RuntimeError("Failed to extract direct link from tmpfiles CDN")

from typing import Any

# Inject API token into environment


import httpx
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageDraw

from .settings import ROOT, settings

app = FastAPI(title="Split & Stitch API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=ROOT / "app" / "static"), name="static")
app.mount("/data", StaticFiles(directory=ROOT / "data"), name="data")
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
            "-vf", "scale=-2:'min(720,ih)'",
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



    w, h = 300, 40
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # Drop shadow
    d.text((w // 2 + 1, h // 2 + 1), "made with split & stitch", fill=(0, 0, 0, 200), anchor="mm")
    d.text((w // 2 - 1, h // 2 - 1), "made with split & stitch", fill=(0, 0, 0, 200), anchor="mm")
    # Main text
    d.text((w // 2, h // 2), "made with split & stitch", fill=(255, 255, 255, 230), anchor="mm")
    img.save(path)
    return path
    w, h = 300, 40
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([(0, 0), (w, h)], radius=20, fill=(0, 0, 0, 160))
    # Standard text, since we can't guarantee an italic font exists on the server
    d.text((w // 2, h // 2), "made with split & stitch", fill=(255, 255, 255, 230), anchor="mm")
    img.save(path)
    return path

def restore_audio_and_mux(source_video: Path | None, stitched_video: Path, final_output: Path) -> Path:
    if not source_video or not source_video.exists():
        run("ffmpeg", "-y", "-i", str(stitched_video), "-c:v", "copy", str(final_output))
        return final_output
    try:
        meta = probe(source_video)
        if meta.get("has_audio"):
            run("ffmpeg", "-y", "-i", str(stitched_video), "-i", str(source_video), "-map", "0:v:0", "-map", "1:a:0?", "-c:v", "copy", "-c:a", "aac", "-shortest", str(final_output))
        else:
            run("ffmpeg", "-y", "-i", str(stitched_video), "-c:v", "copy", str(final_output))
        return final_output
    except:
        run("ffmpeg", "-y", "-i", str(stitched_video), "-c:v", "copy", str(final_output))
        return final_output

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
    ap = next(directory.glob("audio*"), None)
    
    update_job(job_id, stage="Resuming...", failed=False, error=None)
    background.add_task(
        process,
        job_id=job_id,
        video=vp,
        character=cp,
        audio=ap,
        max_duration=job.get("max_duration", 2),
        resolution=job.get("resolution", "Low Res"),
        output_dir=directory,
        resume_from_chunk=job.get("current_chunk", 1),
        engine=job.get("engine", "wan22")
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
