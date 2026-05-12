# Image Scraper to PDF

Aplikasi web Flask untuk scraping gambar dari halaman website dan mengkonversinya menjadi satu file PDF.

## Cara Menjalankan

### 1. Install dependensi dan Playwright browser

```bash
pip install -r requirements.txt
playwright install chromium
```

### 2. Jalankan aplikasi

```bash
python app.py
```

Buka browser ke `http://localhost:5000`

## Fitur

- Input URL website, scrape semua gambar dari halaman tersebut
- Hanya ambil URL gambar yang valid (JPG, JPEG, PNG, WEBP)
- Hapus duplicate gambar otomatis
- Urutkan gambar berdasarkan nomor pada nama file/URL
- Download gambar secara paralel (8 thread) untuk kecepatan maksimal
- Konversi semua gambar menjadi 1 file PDF (setiap gambar = 1 halaman)
- Nama PDF otomatis dari judul halaman website
- File sementara dihapus otomatis setelah PDF selesai
- User dapat download hasil PDF langsung dari browser

## Teknologi

- Python 3.8+
- Flask — web framework
- Playwright — browser automation untuk bypass Cloudflare & JS-rendered content
- BeautifulSoup4 — HTML parser untuk scraping
- Pillow — image processing & PDF generation
- Requests — untuk download gambar setelah URL berhasil diambil

## Struktur Project

```
image-scraper/
├── app.py              # Main Flask application
├── requirements.txt    # Python dependencies
├── templates/
│   └── index.html      # Halaman UI
├── static/
│   ├── css/style.css   # Styling
│   └── js/app.js       # Frontend logic & polling
├── temp/               # File gambar sementara (auto-cleanup)
└── output/             # File PDF hasil konversi
```

## Deployment di Render

### Method 1: Menggunakan render.yaml (Recommended)

File `render.yaml` sudah dikonfigurasi untuk:
- Install Playwright Chromium browser secara otomatis
- Set environment variable `PLAYWRIGHT_BROWSERS_PATH=0`
- Menjalankan aplikasi dengan Gunicorn

Cukup push ke repository dan Render akan automatically detect & deploy.

### Method 2: Manual Build Command

Jika tidak menggunakan `render.yaml`, set build command di Render dashboard:

```bash
bash build.sh
```

Atau set start command menjadi:

```bash
playwright install chromium && gunicorn -w 1 -b 0.0.0.0 app:app
```

### Notes untuk Render:
- Playwright memerlukan ~200MB storage untuk Chromium browser
- Gunakan Python 3.11+ untuk compatibility terbaik
- Recommendation: 1 worker Gunicorn (`-w 1`) karena Playwright resource-intensive
- File PDF temporary akan auto-cleanup setelah download
