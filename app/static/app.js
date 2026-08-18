const $ = id => document.getElementById(id);

let jobId = null;
let pollInterval = null;

// Initialize app & backend connectivity check
async function initApp() {
  const statusPill = $('status-pill');
  const statusText = $('status-text');
  const banner = $('preflight-banner');
  const startBtn = $('start-btn');

  try {
    const res = await fetch('/api/preflight');
    const data = await res.json();

    if (data.ready) {
      statusPill.className = 'status-pill ready';
      if (data.mode === 'mock') {
        statusText.textContent = 'Mock Mode (Simulated)';
      } else if (data.mode === 'hf_space') {
        statusText.textContent = 'ZeroGPU Online';
      } else {
        statusText.textContent = 'ComfyUI Ready';
      }
      startBtn.disabled = false;
      banner.hidden = true;
    } else {
      statusPill.className = 'status-pill error';
      statusText.textContent = 'Setup Needed';
      banner.innerHTML = `<strong>Backend Notice:</strong><br>${(data.problems || ['Backend not ready']).join('<br>')}`;
      banner.hidden = false;
      startBtn.disabled = true;
    }
  } catch (err) {
    statusPill.className = 'status-pill error';
    statusText.textContent = 'Backend Offline';
    banner.innerHTML = '<strong>Connection Error:</strong> Could not connect to API server.';
    banner.hidden = false;
    startBtn.disabled = true;
  }
}

// Setup Interactive File Dropzones & Previews
function setupDropzones() {
  const videoInput = $('video-input');
  const videoDropzone = $('video-dropzone');
  const videoPlaceholder = $('video-placeholder');
  const videoPreviewWrap = $('video-preview-wrap');
  const videoThumb = $('video-thumb');
  const videoFilename = $('video-filename');

  const charInput = $('character-input');
  const charDropzone = $('character-dropzone');
  const charPlaceholder = $('character-placeholder');
  const charPreviewWrap = $('character-preview-wrap');
  const charThumb = $('character-thumb');
  const charFilename = $('character-filename');

  // Video drop & change
  videoInput.addEventListener('change', e => {
    const file = e.target.files[0];
    if (file) {
      const url = URL.createObjectURL(file);
      videoThumb.src = url;
      videoThumb.play().catch(() => {});
      videoFilename.textContent = file.name;
      videoPlaceholder.hidden = true;
      videoPreviewWrap.hidden = false;
    }
  });

  // Character drop & change
  charInput.addEventListener('change', e => {
    const file = e.target.files[0];
    if (file) {
      const reader = new FileReader();
      reader.onload = ev => {
        charThumb.src = ev.target.result;
        charFilename.textContent = file.name;
        charPlaceholder.hidden = true;
        charPreviewWrap.hidden = false;
      };
      reader.readAsDataURL(file);
    }
  });

  // Drag over animations
  [videoDropzone, charDropzone].forEach(dropzone => {
    ['dragenter', 'dragover'].forEach(eventName => {
      dropzone.addEventListener(eventName, e => {
        e.preventDefault();
        dropzone.classList.add('dragover');
      });
    });
    ['dragleave', 'drop'].forEach(eventName => {
      dropzone.addEventListener(eventName, () => {
        dropzone.classList.remove('dragover');
      });
    });
  });
}

// Form Submission & Generation
$('swap-form').addEventListener('submit', async e => {
  e.preventDefault();

  const videoFile = $('video-input').files[0];
  const charFile = $('character-input').files[0];
  const duration = $('duration-select').value;
  const resolution = $('resolution-select').value;

  if (!videoFile || !charFile) {
    alert('Please select both a source video and a character image.');
    return;
  }

  const formData = new FormData();
  formData.append('video', videoFile);
  formData.append('character', charFile);
  formData.append('max_duration', duration);
  formData.append('resolution', resolution);

  const startBtn = $('start-btn');
  const progressSection = $('progress-section');
  const resultSection = $('result-section');

  startBtn.disabled = true;
  progressSection.hidden = false;
  resultSection.hidden = true;
  updateProgress(10, 'Uploading Media...', 'Sending video and character to ZeroGPU');

  try {
    const res = await fetch('/api/jobs', { method: 'POST', body: formData });
    const rawText = await res.text();
    let jobData = null;
    try {
      jobData = JSON.parse(rawText);
    } catch (parseErr) {
      const cleanMsg = rawText.replace(/<[^>]*>?/gm, '').trim();
      throw new Error(cleanMsg || `Server returned status ${res.status}`);
    }

    if (!res.ok) {
      const errorMsg = jobData?.detail?.message || jobData?.detail || jobData?.error || `Request failed with status ${res.status}`;
      throw new Error(errorMsg);
    }

    jobId = jobData.id;

    if (pollInterval) clearInterval(pollInterval);
    pollInterval = setInterval(pollJobStatus, 1500);
    pollJobStatus();
  } catch (err) {
    updateProgress(0, 'Failed', err.message);
    startBtn.disabled = false;
    alert('Generation error: ' + err.message);
  }
});

// Poll Backend Status
async function pollJobStatus() {
  if (!jobId) return;

  try {
    const res = await fetch(`/api/jobs/${jobId}`);
    if (!res.ok) return;
    const job = await res.json();

    const stage = job.stage || 'Processing...';
    const progress = job.progress || 0;

    let detail = 'Executing Wan2.2 Animate diffusion';
    if (stage.includes('Uploading')) detail = 'Transferring media to Hugging Face ZeroGPU';
    else if (stage.includes('Queuing')) detail = 'Waiting for ZeroGPU worker slot';
    else if (stage.includes('Downloading')) detail = 'Retrieving character-swapped video';
    else if (stage.includes('Restoring')) detail = 'Finalizing audio sync & encoding';

    updateProgress(progress, stage, detail);

    if (job.failed) {
      clearInterval(pollInterval);
      const rawError = job.error || 'Unknown error occurred';
      const cleanError = rawError.replace(/<[^>]*>?/gm, '').replace(/&nbsp;/g, ' ').trim();
      updateProgress(0, 'Generation Failed', cleanError);
      $('start-btn').disabled = false;
    }

    if (job.complete) {
      clearInterval(pollInterval);
      const downloadUrl = `/api/jobs/${jobId}/download`;
      const resultVideo = $('result-video');
      const downloadBtn = $('download-btn');

      resultVideo.src = downloadUrl;
      downloadBtn.href = downloadUrl;

      $('progress-section').hidden = true;
      $('result-section').hidden = false;
      $('start-btn').disabled = false;
    }
  } catch (err) {
    console.error('Polling error:', err);
  }
}

function updateProgress(percent, stageText, detailText) {
  $('progress-bar').style.width = `${percent}%`;
  $('progress-percent').textContent = `${percent}%`;
  $('stage-label').textContent = stageText;
  if (detailText) $('stage-detail').textContent = detailText;
}

// Reset Handler
$('reset-btn').addEventListener('click', () => {
  $('swap-form').reset();
  $('video-placeholder').hidden = false;
  $('video-preview-wrap').hidden = true;
  $('character-placeholder').hidden = false;
  $('character-preview-wrap').hidden = true;
  $('result-section').hidden = true;
  $('progress-section').hidden = true;
  $('start-btn').disabled = false;
  jobId = null;
});

// Run Setup
setupDropzones();
initApp();
