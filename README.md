# FaceSwap AI (Local/Client Version)

A powerful, chunk-based Video Face Swapping tool built with FastAPI and the Replicate API. This tool processes long videos by chunking them, running GPU-accelerated Face Swaps with GFPGAN Face Enhancement, and seamlessly stitching the audio and video back together.

## Requirements

1. **Python 3.10+**
2. **FFmpeg**: Must be installed and available in your system PATH.
3. **Ngrok**: Required to securely expose your local files to the Replicate GPU container.
4. **Replicate API Token**: You will need a Replicate account and an API token.

## Setup Instructions

1. **Install Dependencies:**
   Open a terminal in this folder and run:
   \\\ash
   pip install -r requirements.txt
   \\\

2. **Set your API Token:**
   Set your Replicate token as an environment variable in your terminal:
   - **Windows (PowerShell):** \\="your_token_here"\
   - **Mac/Linux:** \export REPLICATE_API_TOKEN="your_token_here"\

3. **Start the Backend Server:**
   In the same terminal, run:
   \\\ash
   python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
   \\\

4. **Start the Ngrok Tunnel:**
   Open a *second* terminal and run:
   \\\ash
   ngrok http 8000
   \\\
   *(Note: The backend is hardcoded to look for a specific ngrok URL in the codebase. If you are running this on a new machine, you must update the \
grok_base\ variable in \pp/main.py\ to match the public URL ngrok gives you).*

5. **Open the Studio:**
   Open your browser and navigate to: [http://127.0.0.1:8000](http://127.0.0.1:8000)

## Security Features

- **Upload Limits:** Capped at 2GB for videos and 10MB for images to prevent out-of-memory crashes.
- **Duration Cap:** Maximum video duration is clamped to 5 minutes to prevent runaway API costs on your Replicate account.
