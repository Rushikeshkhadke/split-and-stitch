# Split and Stitch

A powerful, chunk-based Video Face Swapping tool built with FastAPI and MagicAPI. This tool processes long videos by chunking them to bypass API file limits, running GPU-accelerated Face Swaps, upscaling the final video using RealESRGAN, and seamlessly stitching the audio and video back together.

## Architecture
- **Frontend:** Vanilla JS/HTML/CSS (Ready to deploy on Vercel)
- **Backend:** Python FastAPI (Ready to deploy on Render using the provided Dockerfile)
- **Video Engine:** FFmpeg (Chunking, downscaling to 720p, stitching, audio restoration)
- **AI Engine:** MagicAPI (FaceFusion Video V3 & RealESRGAN Upscaler)
- **Temporary CDN:** Tmpfiles.org (To bypass firewalls blocking cloud workers)

## Setup Instructions (For Vercel/Render Deployment)

### 1. Backend (Render.com)
1. Fork or clone this repository to your GitHub account.
2. Log into Render.com and create a new **Web Service**.
3. Connect your repository. Render will automatically read the Dockerfile to install FFmpeg and Python.
4. Go to the Environment Variables section in Render and add your API key:
   - MAGICAPI_KEY = your_magicapi_key_here
5. Deploy the backend. Render will give you a live backend URL (e.g., https://split-and-stitch-api.onrender.com).

### 2. Frontend (Vercel.com)
1. Open pp/static/app.js and update the API URLs to point to your new Render backend URL instead of relative paths. (e.g., change /api/jobs to https://split-and-stitch-api.onrender.com/api/jobs).
2. Log into Vercel.com and create a new project.
3. Connect the exact same GitHub repository, but set the **Root Directory** to pp/static.
4. Deploy the frontend!

## Security & Optimizations
- **Auto-Downscaling:** The backend automatically downscales 4K/1080p videos to 720p before uploading to save API costs.
- **Firewall Bypass:** Uses an advanced regex scraper to bypass Catbox/tmpfiles CDN firewalls blocking MagicAPI worker nodes.
- **Chunking:** Safely processes long videos in 10-second chunks to avoid API timeouts and memory limits.
