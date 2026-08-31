# Split & Stitch AI

A powerful, chunk-based Video Face Swapping SaaS built with FastAPI. This architecture processes long videos by chunking them to bypass serverless API limits, running GPU-accelerated Face Swaps, and seamlessly stitching the audio and video back together using FFmpeg.

## Architecture
- **Frontend:** Vanilla JS/HTML/CSS (Ready to deploy on Vercel)
- **Backend:** Python FastAPI (Ready to deploy on Render using the provided Dockerfile)
- **Video Engine:** FFmpeg (Chunking, stitching, audio restoration)
- **AI Routing Engine:** Dynamically routes to MagicAPI, Fal.ai, or Replicate based on user UI selection.

## Deployment Instructions

### 1. Backend (Render.com)
1. Fork or clone this repository to your GitHub account.
2. Log into Render.com and create a new **Web Service**.
3. Connect your repository. Render will automatically read the Dockerfile to install FFmpeg and Python.
4. Go to the Environment Variables section in Render and add your API keys:
   - MAGICAPI_KEY = your_key_here
   - FAL_KEY = your_key_here
   - REPLICATE_API_TOKEN = your_key_here
5. Deploy the backend. Render will generate a live backend URL (e.g., https://your-backend.onrender.com).

### 2. Frontend (Vercel.com)
1. Open app/static/app.js and change the API_BASE variable on line 1 to your new Render backend URL.
2. Log into Vercel.com and create a new project.
3. Connect the exact same GitHub repository, but set the **Root Directory** to app/static.
4. Deploy the frontend!

## Features
- **Social Sharing:** Native Web Share API integration with automatic OpenGraph SEO tags for iMessage/WhatsApp.
- **Chunking Engine:** Safely processes long videos in 10-second chunks to avoid API timeouts and memory limits.
