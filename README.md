# Character Swap — Wan2.2 Animate

This is a minimal FastAPI application for one-job-at-a-time character replacement:

1. upload a video and one character image;
2. the server analyses and normalizes the video, determines a conservative segment length, splits it, calls Wan2.2 Animate in **Mix** mode through ComfyUI, retries failed pieces, stitches the output, and restores the original audio;
3. preview or download a single `final_character_swap.mp4`.

## Configuration

The FastAPI backend and ComfyUI worker are intentionally separate. The browser user only uploads two files; the backend transfers each segment and the character image to the worker over ComfyUI's HTTP API, queues the Wan workflow, retrieves the generated video through the worker's `/view` endpoint, then stitches the downloads locally.

Copy `.env.example` to `.env` on the **backend machine** and set:

| Variable | Purpose |
|---|---|
| `MODE=real` | Uses the remote Wan2.2 worker. `mock` is only a media-pipeline test mode and does no character replacement. |
| `COMFYUI_URL` | Base URL of any reachable compatible ComfyUI server, for example `http://127.0.0.1:8188` or `https://comfyui.example.net`. This is the only setting used to select the ComfyUI worker. |
| `WAN_WORKFLOW_PATH` | Local backend path to the API-format Wan2.2 Animate **Mix** workflow. |
| `STORAGE_DIR` | Local backend directory for uploaded media, intermediate segments, and final videos. |

Use an **API-format** workflow exported from ComfyUI (Save (API Format)), based on the official Wan2.2 Animate Mix workflow. Keep it on the backend machine at the `WAN_WORKFLOW_PATH` location.

Save it as `workflow/wan22_animate_mix_api.json`. Every value that should be set by this service must use these literal tokens:

| Token | Meaning |
|---|---|
| `{{video}}` | uploaded segment filename in ComfyUI's `input` folder |
| `{{character}}` | uploaded reference-image filename in ComfyUI's `input` folder |
| `{{output_prefix}}` | unique generated output prefix |
| `{{frames}}` | segment frame count |
| `{{mode}}` | must be connected to the Animate node's mode input; resolves to `Mix` |

All other workflow settings (model, sampler, VAE, text encoder, pose preprocessing) are fixed in that template. This deliberately keeps them out of the product UI.

The backend does not impose a GPU model or VRAM policy. Choose the Wan2.2 Animate Mix workflow and worker configuration appropriate for that machine. The official Animate workflow is built around 77-frame extensions, so the backend never gives an independent segment more than 77 frames.

## Run the backend locally

Install Python 3.10+ and FFmpeg on the backend machine. Then install dependencies, configure `.env` as above, and start:

```powershell
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`. In `real` mode, preflight checks the selected ComfyUI server and local workflow file. In `mock` mode, only the backend FFmpeg requirement is checked.

## Connect any compatible ComfyUI worker

Install and start a compatible ComfyUI server with the Wan2.2 Animate Mix workflow, its required nodes, and model files. The worker can be on the backend machine, a local network host, or a separately hosted GPU server. If it is separate, make its HTTP API reachable from the backend machine and set `COMFYUI_URL` to that server's base URL.

From the ComfyUI installation directory, a typical worker command is:

```bash
python main.py --listen 0.0.0.0 --port 8188
```

The backend sends no authentication headers, so use a network arrangement that permits the backend to reach the worker. Authentication and production infrastructure are intentionally out of scope for this version.

### Verify the worker before generation

On the backend machine, run the explicit connectivity test:

```powershell
$env:COMFYUI_URL='https://comfyui.example.net'
python scripts/test_comfyui_connectivity.py
```

It performs exactly `GET {COMFYUI_URL}/system_stats`. Continue only when it prints both `REACHABLE` and `GPU DETECTED`. Then start the FastAPI backend. `/api/preflight` reports four independent values: ComfyUI reachable, GPU detected, Wan2.2 model detected, and workflow detected. It returns clear failures when the server cannot be reached, does not report a GPU, lacks Wan Animate nodes/model options, or the local workflow is missing/invalid.

Official references: [Wan2.2 Animate in ComfyUI](https://docs.comfy.org/tutorials/video/wan/wan2-2-animate) and [Wan2.2 source/models](https://github.com/Wan-Video/Wan2.2).
