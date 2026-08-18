import sys
import json
import time
import httpx
from pathlib import Path

proj_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(proj_root))

from app.settings import settings
from app.main import probe, safe_segment_seconds, make_mock_badge

print("=" * 70)
print("COMPREHENSIVE CHARACTER SWAP APPLICATION VERIFICATION")
print("=" * 70)

# ----------------------------------------------------------------------------
# A. ENVIRONMENT CHECKS
# ----------------------------------------------------------------------------
print("\n[A] ENVIRONMENT CHECKS:")
print(f"  Python Version: {sys.version.split()[0]}")
try:
    import torch
    print(f"  PyTorch Version: {torch.__version__}")
    print(f"  CUDA Available: {torch.cuda.is_available()}")
    if torch.cuda.is_available():
        print(f"  GPU Device: {torch.cuda.get_device_name(0)}")
        props = torch.cuda.get_device_properties(0)
        print(f"  VRAM: {round(props.total_memory / (1024**3), 2)} GB")
except Exception as e:
    print(f"  Torch error: {e}")

# ----------------------------------------------------------------------------
# B. COMFYUI CHECKS
# ----------------------------------------------------------------------------
print("\n[B] COMFYUI CHECKS (http://127.0.0.1:8188):")
comfy_url = "http://127.0.0.1:8188"
try:
    r_stats = httpx.get(f"{comfy_url}/system_stats", timeout=5)
    print(f"  GET /system_stats -> HTTP {r_stats.status_code}")
    stats_data = r_stats.json()
    print(f"  ComfyUI Version: {stats_data.get('system', {}).get('comfyui_version')}")
    print(f"  Devices: {stats_data.get('devices', [{}])[0].get('name')}")
    
    r_obj = httpx.get(f"{comfy_url}/object_info", timeout=5)
    print(f"  GET /object_info -> HTTP {r_obj.status_code}")
    obj_data = r_obj.json()
    print(f"  Total Node Types Registered: {len(obj_data)}")
    
    # Custom nodes check
    required_nodes = ["DWPreprocessor", "Sam2Segmentation", "PointsEditor", "WanAnimateToVideo", "BlockifyMask", "GrowMask", "DrawMaskOnImage", "PixelPerfectResolution"]
    for rn in required_nodes:
        print(f"    - Node '{rn}': {'AVAILABLE' if rn in obj_data else 'MISSING'}")
except Exception as e:
    print(f"  ComfyUI check error: {e}")

# ----------------------------------------------------------------------------
# C. WORKFLOW DAG VALIDATION
# ----------------------------------------------------------------------------
print("\n[C] WORKFLOW DAG VALIDATION:")
wf_path = proj_root / "workflow" / "wan22_animate_mix_api.json"
print(f"  Workflow file: {wf_path}")
print(f"  Workflow exists: {wf_path.exists()} ({wf_path.stat().st_size} bytes)")
if wf_path.exists():
    wf_json = json.loads(wf_path.read_text(encoding="utf-8"))
    print(f"  Total nodes in API graph: {len(wf_json)}")
    tokens = ["{{video}}", "{{character}}", "{{output_prefix}}", "{{frames}}", "{{mode}}"]
    wf_text = json.dumps(wf_json)
    for tok in tokens:
        print(f"    - Dynamic token '{tok}': {'FOUND' if tok in wf_text else 'MISSING'}")

# ----------------------------------------------------------------------------
# D. FASTAPI BACKEND CHECKS
# ----------------------------------------------------------------------------
print("\n[D] FASTAPI BACKEND CHECKS (http://127.0.0.1:8000):")
api_url = "http://127.0.0.1:8000"
try:
    r_root = httpx.get(f"{api_url}/", timeout=5)
    print(f"  GET / -> HTTP {r_root.status_code} ({len(r_root.text)} bytes)")
    
    r_pref = httpx.get(f"{api_url}/api/preflight", timeout=5)
    print(f"  GET /api/preflight -> HTTP {r_pref.status_code}")
    print(f"    Preflight Response: {json.dumps(r_pref.json(), indent=4)}")
    
    r_conn = httpx.get(f"{api_url}/api/connectivity", timeout=5)
    print(f"  GET /api/connectivity -> HTTP {r_conn.status_code}")
    print(f"    Connectivity Response: {json.dumps(r_conn.json(), indent=4)}")
except Exception as e:
    print(f"  Backend check error: {e}")

# ----------------------------------------------------------------------------
# E. END-TO-END PIPELINE RUN
# ----------------------------------------------------------------------------
print("\n[E] END-TO-END PIPELINE RUN (UPLOAD -> PROCESS -> PREVIEW -> DOWNLOAD):")
scratch_dir = Path(r"C:\Users\khadk\.gemini\antigravity\brain\00657df0-ef7d-495e-9f66-d4831ce860dc\scratch")
video_file = scratch_dir / "test_video.mp4"
image_file = scratch_dir / "test_character.png"

try:
    # 1. Upload
    with open(video_file, "rb") as vf, open(image_file, "rb") as cf:
        files = {
            "video": ("test_video.mp4", vf, "video/mp4"),
            "character": ("test_character.png", cf, "image/png")
        }
        r_upload = httpx.post(f"{api_url}/api/jobs", files=files, timeout=10)
        print(f"  POST /api/jobs -> HTTP {r_upload.status_code}")
        job_info = r_upload.json()
        job_id = job_info["id"]
        print(f"  Created Job ID: {job_id}")

    # 2. Poll Status
    print(f"  Polling progress for job {job_id}...")
    last_stage = ""
    while True:
        time.sleep(0.5)
        r_poll = httpx.get(f"{api_url}/api/jobs/{job_id}", timeout=5)
        p = r_poll.json()
        if p.get("stage") != last_stage:
            print(f"    [Job Stage] {p.get('stage')} (Progress: {p.get('progress')}%)")
            last_stage = p.get("stage")
        if p.get("complete") or p.get("failed"):
            break

    print(f"  Final Job State: complete={p.get('complete')}, progress={p.get('progress')}%")
    assert p.get("complete") is True

    # 3. Download & Verify Video
    r_down = httpx.get(f"{api_url}/api/jobs/{job_id}/download", timeout=10)
    print(f"  GET /api/jobs/{job_id}/download -> HTTP {r_down.status_code}")
    print(f"  Content-Type: {r_down.headers.get('content-type')}")
    print(f"  Downloaded Video Size: {len(r_down.content)} bytes")
    assert r_down.status_code == 200
    assert len(r_down.content) > 0
    print("  >>> END-TO-END PIPELINE TEST: 100% SUCCESSFUL!")
except Exception as e:
    print(f"  Pipeline run error: {e}")

print("\n" + "=" * 70)
print("VERIFICATION COMPLETED")
print("=" * 70)
