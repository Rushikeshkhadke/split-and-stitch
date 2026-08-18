"""Run on the local backend machine before starting a real generation.

Usage (PowerShell): $env:COMFYUI_URL='https://comfyui.example.net'; python scripts/test_comfyui_connectivity.py
"""
import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

url = os.environ.get("COMFYUI_URL", "").rstrip("/")
if not url:
    raise SystemExit("Set COMFYUI_URL to the reachable ComfyUI server URL first.")
try:
    with urlopen(f"{url}/system_stats", timeout=15) as response:
        body = json.load(response)
except (HTTPError, URLError, TimeoutError) as exc:
    raise SystemExit(f"FAILED: GET {url}/system_stats could not reach ComfyUI: {exc}")

devices = body.get("devices", [])
gpu = next((d for d in devices if str(d.get("type", "")).lower() not in {"", "cpu"}), None)
print(f"REACHABLE: GET {url}/system_stats returned HTTP 200")
if gpu:
    print(f"GPU DETECTED: {gpu.get('name', 'ComfyUI GPU')} (vram_total={gpu.get('vram_total', 'unknown')})")
else:
    print("GPU NOT DETECTED: ComfyUI responded but reported no GPU device.")
