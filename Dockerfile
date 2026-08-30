FROM python:3.11-slim

# Install system dependencies, specifically ffmpeg for video chunking
RUN apt-get update && \
    apt-get install -y ffmpeg && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /code

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY app/ ./app/

# Render sets the PORT environment variable dynamically
CMD uvicorn app.main:app --host 0.0.0.0 --port 