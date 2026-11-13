# ✦ Gemini Watermark Remover

A free, 100%-local web app that removes the visible **Gemini AI watermark** from
photos and videos. You drag a box around the badge — **only those pixels are
edited**, everything else stays byte-identical to the original.

No accounts. No cloud uploads. No watermark on your output.

---

## ✨ Features

- **Pixel-exact manual selection** — you draw the box, so nothing is ever
  mis-detected or blurred by accident. A live preview shows the exact pixels
  about to be cleaned.
- **Region-only editing** — every pixel outside your selection passes through
  untouched (verified byte-identical).
- **Texture-aware inpainting** — Navier–Stokes fill + Telea fringe pass +
  grain matching, so the cleaned patch blends into the surroundings instead of
  looking like a smooth rectangle.
- **Original quality output**
  - Photos: saved at original resolution (quality 100).
  - Video: encoded **once** with x264 (CRF 17, visually lossless), same
    resolution + FPS, original audio track kept.
- **Fast** — crop-only processing (~45 ms per 1080p frame) and a single-pass
  streaming video encode (no temp files, no double transcode).
- **Private by design** — the engine runs on your machine. Nothing is uploaded
  to any server.
- **Free & unlimited** — no accounts, no export limits, no branding.

## 🚀 Quick start (local)

```bash
git clone https://github.com/<your-username>/gemini-watermark-remover.git
cd gemini-watermark-remover
pip install -r requirements.txt
python app.py
```

Open **http://127.0.0.1:5000** — or just double-click `start.bat` on Windows.

> Python 3.10+ recommended. FFmpeg is bundled via `imageio-ffmpeg` — nothing
> else to install.

## 🕹 How to use

1. **Upload** — drop a photo or video onto the drop zone (PNG/JPG/WebP/BMP/TIFF,
   MP4/MOV/M4V/WebM/MKV/AVI).
2. **Select** — drag a tight box around the visible watermark. For videos,
   scrub/tap to a frame where the badge is sharp. A preview crop confirms the
   exact region.
3. **Remove** — press the button. Photos finish in seconds; videos are cleaned
   across *every frame* in one pass (the mask is the union of 5 sampled frames,
   so it never flickers).
4. **Download** — original resolution for photos; visually lossless MP4 with
   original audio for video.

## ☁️ Free deployment

The repo ships with everything needed to run in the cloud for free.
Note: free tiers **sleep when idle** and have **ephemeral storage** —
processed files disappear when the instance restarts, which is fine for a
removal tool.

### Option A — Render (easiest)

1. Push this repo to GitHub.
2. On [render.com](https://render.com): **New + → Blueprint**, select your repo.
   The included `render.yaml` (Docker runtime, free plan) is picked up
   automatically.
3. Done — you get a `https://<app>.onrender.com` URL.

### Option B — Hugging Face Spaces (most free RAM — best for video)

1. Create a new Space → SDK: **Docker**.
2. Point it at your GitHub repo (or upload these files).
3. The `Dockerfile` already listens on port `7860` (Spaces' expected port) and
   declares the OpenCV system libs. Free tier: 2 vCPU / 16 GB RAM.

### Option C — any Docker host

`docker build -t gwm-remover . && docker run -p 5000:5000 gwm-remover`
(the app binds to `0.0.0.0` and reads the `PORT` env var automatically).

> SEO is already wired in: 11 keyword-targeted pages, JSON-LD structured data
> (WebApplication / HowTo / FAQPage / Article / BreadcrumbList), sitemap.xml
> and robots.txt — canonicals and sitemap URLs auto-adapt to whatever domain
> you deploy on. After deploying, submit `/sitemap.xml` in
> [Google Search Console](https://search.google.com/search-console).

## 🔌 API

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/upload` | POST | multipart file → `{job_id}` |
| `/api/preview/<id>` | POST | `{box:[x,y,w,h]}` → exact-region crop PNG |
| `/api/process/<id>` | POST | `{box:[x,y,w,h]}` → starts removal |
| `/api/status/<id>` | GET | progress, logs, steps, region stats |
| `/api/download/<id>` | GET | cleaned file (`?view=1` to preview inline) |

## 🧠 How it works (tech)

- **Mask:** inside your box, a tight mask of the bright badge pixels is built
  (fallback: box core), then dilated. For video, tight masks from 5 sampled
  frames are unioned so background changes never break the mask.
- **Fill:** `cv2.inpaint` (Navier–Stokes) + Telea fringe pass, with noise
  matched to the local grain.
- **Video pipeline:** frames stream raw into ffmpeg's x264 in a single pass —
  `veryfast` preset, CRF 17, `yuv420p`, audio copied as AAC 192k.

## 📁 Project structure

```
├── app.py            # Flask server + job engine (upload/select/process/download)
├── remover.py        # clamp_box, mask_from_box, clean_with_mask, remove_region
├── static/
│   ├── index.html    # the tool UI (single file, no build step)
│   ├── site.css      # shared styles for content pages
│   └── favicon.svg
├── templates/        # SEO pages (how-it-works, features, guides, faq, ...)
├── Dockerfile        # Render / HF Spaces / any Docker host
├── render.yaml       # Render blueprint
├── requirements.txt
└── start.bat         # Windows launcher
```

## ⚠️ Honest limits & responsible use

- Only the **visible** badge is removed. Google's invisible **SynthID**
  signature may survive — this tool doesn't add, remove, or alter hidden
  watermarks.
- **Use only on content you own or have rights to modify** (e.g. media you
  generated yourself). Removing badges from other people's content to
  misrepresent its origin may violate their terms and local law.
- Not affiliated with or endorsed by Google.

## 📄 License

[MIT](LICENSE)
