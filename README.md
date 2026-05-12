# Image Scraper to PDF

Aplikasi web Flask untuk scraping gambar dari halaman website dan mengkonversinya menjadi satu file PDF.

## Cara Menjalankan

### 1. Install dependensi

```bash
pip install -r requirements.txt
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
- Requests — HTTP client dengan User-Agent browser
- BeautifulSoup4 — HTML parser untuk scraping
- Pillow — image processing & PDF generation

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
