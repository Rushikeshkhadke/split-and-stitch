const API_BASE = (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1') 
    ? 'http://127.0.0.1:8000' 
    : 'https://split-and-stitch.onrender.com';

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
    const res = await fetch(API_BASE + '/api/preflight');
    const data = await res.json();

    if (data.ready) {
      statusPill.className = 'status-pill ready';
      if (data.mode === 'mock') {
        statusText.textContent = 'Pipeline Test Mode (Instant Split & Stitch)';
      } else if (data.mode === 'hf_space') {
        
        const engineSelect = document.getElementById('engine-select').value;
        if (engineSelect === 'magicapi') {
            statusText.textContent = 'MagicAPI FaceFusion Online';
            document.querySelector('.app-footer p').textContent = 'FaceSwap AI • Powered by MagicAPI FaceFusion';
        } else if (engineSelect === 'fal') {
            statusText.textContent = 'Fal.ai Engine Online';
            document.querySelector('.app-footer p').textContent = 'FaceSwap AI • Powered by Fal.ai Serverless';
        } else {
            statusText.textContent = 'Replicate Engine Online';
            document.querySelector('.app-footer p').textContent = 'FaceSwap AI • Powered by Replicate A100 GPU';
        }

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
      videoThumb.onloadedmetadata = () => {
        const dur = videoThumb.duration;
        if (dur && !isNaN(dur)) {
          const autoOpt = $('duration-select').querySelector('option[value="auto"]');
          if (autoOpt) {
            autoOpt.textContent = `Auto (Full Video: ${dur.toFixed(1)}s)`;
          }
          videoFilename.textContent = `${file.name} (${dur.toFixed(1)}s)`;
        }
      };
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

  // Drag over animations & drop handling
  [videoDropzone, charDropzone].forEach(dropzone => {
    ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
      dropzone.addEventListener(eventName, e => {
        e.preventDefault();
        e.stopPropagation();
      });
    });

    ['dragenter', 'dragover'].forEach(eventName => {
      dropzone.addEventListener(eventName, () => {
        dropzone.classList.add('dragover');
      });
    });

    ['dragleave', 'drop'].forEach(eventName => {
      dropzone.addEventListener(eventName, () => {
        dropzone.classList.remove('dragover');
      });
    });
  });

  videoDropzone.addEventListener('drop', e => {
    e.preventDefault();
    videoDropzone.classList.remove('dragover');
    if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      videoInput.files = e.dataTransfer.files;
      videoInput.dispatchEvent(new Event('change', { bubbles: true }));
    }
  });

  charDropzone.addEventListener('drop', e => {
    e.preventDefault();
    charDropzone.classList.remove('dragover');
    if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      charInput.files = e.dataTransfer.files;
      charInput.dispatchEvent(new Event('change', { bubbles: true }));
    }
  });
}

// Form Submission & Generation
$('swap-form').addEventListener('submit', async e => {
  e.preventDefault();

  const videoFile = $('video-input').files[0];
  const charFile = $('character-input').files[0];
  const duration = $('duration-select').value;

  if (!videoFile || !charFile) {
    alert('Please select both a source video and a character image.');
    return;
  }

  const startBtn = $('start-btn');
  const progressSection = $('progress-section');
  const resultSection = $('result-section');

  startBtn.disabled = true;
  progressSection.hidden = false;
  resultSection.hidden = true;

  try {
    const engine = $('engine-select') ? $('engine-select').value : 'liveportrait';
    const formData = new FormData();
    formData.append('engine', engine);
    formData.append('max_duration', duration);
    formData.append('video', videoFile);
    formData.append('character', charFile);

    updateProgress(35, 'Starting Task...', 'Registering generation job for automatic chunk processing');

    const res = await fetch(API_BASE + '/api/jobs', { method: 'POST', body: formData });
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

    let detail = 'Executing AI model generation';
    if (stage.includes('Uploading')) detail = 'Transferring media to secure processing node';
    else if (stage.includes('Extracting')) detail = 'Separating original audio track';
    else if (stage.includes('Restoring')) detail = 'Muxing original audio back onto generated video';
    else if (stage.includes('Face Swapping')) detail = 'AI model is mapping character features to video frames';

    updateProgress(progress, stage, detail);

    if (job.failed) {
      clearInterval(pollInterval);
      const rawError = job.error || 'Unknown error occurred';
      const cleanError = rawError.replace(/<[^>]*>?/gm, '').replace(/&nbsp;/g, ' ').trim();
      $('stage-label').textContent = 'Generation Failed';
      $('stage-detail').textContent = cleanError;
      $('start-btn').disabled = false;
      $('retry-wrap').hidden = false;
    } else {
      $('retry-wrap').hidden = true;
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
      $('retry-wrap').hidden = true;
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
  
  $('chunk-badge').hidden = true;
}

// Retry Handler
$('retry-btn').addEventListener('click', async () => {
  if (!jobId) return;
  const retryBtn = $('retry-btn');
  retryBtn.disabled = true;
  $('retry-wrap').hidden = true;
  updateProgress(15, 'Resuming Generation...', 'Re-submitting failed chunk to AI Model');

  try {
    const res = await fetch(`/api/jobs/${jobId}/retry`, { method: 'POST' });
    if (!res.ok) throw new Error('Could not resume job');
    if (pollInterval) clearInterval(pollInterval);
    pollInterval = setInterval(pollJobStatus, 1500);
    pollJobStatus();
  } catch (e) {
    alert('Retry error: ' + e.message);
    retryBtn.disabled = false;
    $('retry-wrap').hidden = false;
  }
});

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
  $('retry-wrap').hidden = true;
  $('chunk-badge').hidden = true;
  jobId = null;
});

// Run Setup
setupDropzones();
initApp();


// Share Logic
share-btn.addEventListener('click', async () => {
  const videoUrl = result-video.src;
  if (navigator.share) {
    try {
      await navigator.share({
        title: 'My AI Face Swap',
        text: 'Check out this insane Face Swap I made with Split & Stitch AI!',
        url: videoUrl
      });
    } catch (err) {
      console.log('User cancelled share');
    }
  } else {
    const text = encodeURIComponent('Check out this insane Face Swap I made with Split & Stitch AI! ' + videoUrl);
    window.open('https://twitter.com/intent/tweet?text=' + text, '_blank');
  }
});
