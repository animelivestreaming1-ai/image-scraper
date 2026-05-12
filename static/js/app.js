let pollInterval = null;

function show(id) {
  document.getElementById(id).classList.remove('hidden');
}

function hide(id) {
  document.getElementById(id).classList.add('hidden');
}

function setProgress(percent, detail) {
  document.getElementById('progress-bar').style.width = percent + '%';
  if (detail !== undefined) {
    document.getElementById('progress-detail').textContent = detail;
  }
}

function setStatus(text) {
  document.getElementById('status-text').textContent = text;
}

async function startConvert() {
  const urlInput = document.getElementById('url-input');
  const url = urlInput.value.trim();

  if (!url) {
    urlInput.focus();
    urlInput.style.borderColor = '#ef4444';
    setTimeout(() => { urlInput.style.borderColor = ''; }, 1500);
    return;
  }

  document.getElementById('convert-btn').disabled = true;
  hide('result-card');
  hide('error-card');
  show('progress-card');
  setStatus('Memulai...');
  setProgress(5, '');

  try {
    const res = await fetch('/convert', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url }),
    });
    const data = await res.json();

    if (data.error) {
      showError(data.error);
      return;
    }

    pollJob(data.job_id, data.mode);
  } catch (err) {
    showError('Gagal menghubungi server. Pastikan server berjalan.');
  }
}

function pollJob(jobId, mode) {
  pollInterval = setInterval(async () => {
    try {
      const res = await fetch(`/status/${jobId}`);
      const job = await res.json();

      if (job.status === 'error') {
        clearInterval(pollInterval);
        showError(job.error || 'Terjadi kesalahan tidak dikenal.');
        return;
      }

      if (job.status === 'done') {
        clearInterval(pollInterval);
        setProgress(100, '');
        setTimeout(() => showResult(jobId, job), 300);
        return;
      }

      updateProgress(job, mode);
    } catch (err) {
      console.error('Poll error:', err);
    }
  }, 800);
}

function updateProgress(job, mode) {
  const status = job.status || '';
  const total = job.total || 1;
  const done = job.completed || 0;

  if (status === 'Analyzing URL...' || status === 'starting') {
    setStatus('Menganalisa URL...');
    setProgress(5, '');

  } else if (status.includes('Fetching page 1') || status.includes('detect')) {
    setStatus('Mendeteksi jumlah halaman...');
    setProgress(10, 'Membuka halaman pertama...');

  } else if (status.includes('Found') && status.includes('pages')) {
    setStatus('Halaman terdeteksi!');
    setProgress(15, status);

  } else if (status.includes('Fetching pages')) {
    const pct = Math.min(15 + Math.round((done / total) * 35), 50);
    setStatus('Mengambil semua halaman...');
    setProgress(pct, `${done} dari ${total} halaman diproses`);

  } else if (status === 'Scraping image URLs...') {
    setStatus('Mengambil daftar gambar...');
    setProgress(15, 'Mencari gambar di halaman...');

  } else if (status.includes('Downloading')) {
    const pct = Math.min(50 + Math.round((done / total) * 40), 90);
    setStatus('Mendownload gambar...');
    setProgress(pct, `${done} dari ${total} gambar selesai`);

  } else if (status === 'Building PDF...') {
    setStatus('Membuat PDF...');
    setProgress(95, 'Menggabungkan semua gambar menjadi PDF...');
  }
}

function showResult(jobId, job) {
  hide('progress-card');
  show('result-card');

  document.getElementById('result-info').textContent =
    `${job.image_count} gambar berhasil dikonversi menjadi 1 file PDF.`;

  document.getElementById('download-link').href = `/download/${jobId}`;
  document.getElementById('convert-btn').disabled = false;
}

function showError(message) {
  clearInterval(pollInterval);
  hide('progress-card');
  show('error-card');
  document.getElementById('error-text').textContent = message;
  document.getElementById('convert-btn').disabled = false;
}

function resetForm() {
  hide('result-card');
  hide('error-card');
  hide('progress-card');
  document.getElementById('url-input').value = '';
  document.getElementById('url-input').focus();
  document.getElementById('convert-btn').disabled = false;
}

document.getElementById('url-input').addEventListener('keydown', function (e) {
  if (e.key === 'Enter') startConvert();
});
