"""
Gemini Watermark Remover - local web app (manual select mode).

Flow: upload -> user drags a box over the watermark -> remove -> download.
Only pixels inside the selected box are ever modified. Video is piped
straight into x264 (single encode, preset veryfast) for speed.
Live activity streams via polling /api/status/<job_id>.
"""
import os
import uuid
import time
import threading
import subprocess
import traceback

import cv2
import numpy as np
import imageio_ffmpeg

try:
    # Small shared deploy CPUs: fewer OpenCV threads = less contention/RAM.
    # Inpainting quality is identical; only parallelism changes.
    cv2.setNumThreads(2)
except Exception:
    pass
from flask import Flask, request, jsonify, send_file, send_from_directory, render_template, Response, redirect
from flask_cors import CORS
from werkzeug.utils import secure_filename

from remover import clamp_box, mask_from_box, clean_with_bbox, mask_bbox
from translations import LANGS, META, merged

BASE = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE, "uploads")
OUTPUT_DIR = os.path.join(BASE, "outputs")
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

ALLOWED_IMG = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"}
ALLOWED_VID = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}

jobs = {}

# ---- production guards (Render free ≈ 512 MB RAM) ----
MAX_UPLOAD_BYTES = 500 * 1024 * 1024
VIDEO_CONCURRENCY = 1          # one video encode at a time: each holds a frame + ffmpeg pipe
_video_slot = threading.Semaphore(VIDEO_CONCURRENCY)
JOB_TTL = 3600                 # finished jobs + their files are swept after 1h
JOB_CAP = 60


def _prune_jobs():
    """Drop finished jobs older than JOB_TTL (or beyond JOB_CAP) and delete
    their files. Keeps RAM (jobs dict) and ephemeral disk bounded."""
    try:
        now = time.time()
        for jid in list(jobs.keys()):
            j = jobs.get(jid)
            if not j or not j.get("done"):
                continue
            age = now - j.get("t_end", j.get("t0", now))
            if age < JOB_TTL and len(jobs) <= JOB_CAP:
                continue
            for p in (j.get("src"), j.get("dst"), j.get("preview")):
                try:
                    if p and os.path.exists(p):
                        os.remove(p)
                except OSError:
                    pass
            jobs.pop(jid, None)
    except Exception:
        pass

STEPS_IMAGE = ["upload", "select", "remove", "done"]
STEPS_VIDEO = ["upload", "select", "remove", "encode", "done"]
STEP_LABELS = {"upload": "Upload", "select": "Select region",
               "remove": "Remove", "encode": "Encode video", "done": "Done"}

app = Flask(__name__, static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES
CORS(app)


# ---------------- i18n: multi-language multi-page site ----------------
SEO_PAGES = {
    "how-it-works": "how-it-works.html",
    "features": "features.html",
    "guides": "guides.html",
    "guides/remove-gemini-watermark-from-photos": "guide-photo.html",
    "guides/remove-gemini-watermark-from-videos": "guide-video.html",
    "guides/what-is-the-gemini-watermark": "guide-what.html",
    "faq": "faq.html",
    "about": "about.html",
    "privacy": "privacy.html",
    "terms": "terms.html",
}


def detect_lang():
    """Pick the best language from the Accept-Language header."""
    header = request.headers.get("Accept-Language", "")
    best, bestq = "en", 0.0
    for part in header.split(","):
        seg = part.strip().split(";q=")
        base = seg[0].strip().lower().split("-")[0]
        try:
            q = float(seg[1]) if len(seg) > 1 else 1.0
        except ValueError:
            q = 0.0
        if base in LANGS and q > 0 and q > bestq:
            best, bestq = base, q
    return best


@app.context_processor
def i18n_context():
    """Expose the merged translation dict, language prefix and hreflang
    alternates for the current path to every template."""
    parts = request.path.split("/")
    lang = parts[1] if len(parts) > 1 and parts[1] in LANGS else "en"
    rest = "/" + "/".join(parts[2:]) if lang in LANGS and len(parts) > 2 else request.path
    if lang not in LANGS:
        rest = request.path
    root = request.url_root.rstrip("/")
    if rest == "/" or rest == "":
        alt = {l: f"{root}/{l}/" for l in LANGS}
        self_url = f"{root}/{lang}/"
    else:
        alt = {l: f"{root}/{l}{rest}" for l in LANGS}
        self_url = f"{root}/{lang}{rest}"
    return {
        "lang": lang,
        "lang_prefix": "/" + lang,
        "t": merged(lang),
        "langs": LANGS,
        "meta_l": META,
        "alt": alt,
        "self_url": self_url,
        "site_url": root,
        "year": time.strftime("%Y"),
    }


# landing page: /<lang>/
@app.route("/<lang>/")
def landing(lang):
    if lang not in LANGS:
        return render_template("404.html"), 404
    return render_template("landing.html")


# tool page: /<lang>/remover
@app.route("/<lang>/remover")
def remover_lang(lang):
    if lang not in LANGS:
        return render_template("404.html"), 404
    return render_template("tool.html")


# /remover serves the English tool page
@app.route("/remover")
def remover_en():
    return render_template("tool.html")


# legacy English-only SEO routes -> permanent redirects to /en/...
def _legacy(page):
    def red():
        return redirect(f"/en/{page}", code=301)
    return red


# localized SEO pages
def _page(tmpl):
    def view(**kwargs):
        return render_template(tmpl)
    return view


for _page_path, _tmpl in SEO_PAGES.items():
    app.add_url_rule(f"/<lang>/{_page_path}",
                     f"page_{_page_path.replace('/', '_').replace('-', '_')}",
                     _page(_tmpl))
    app.add_url_rule(f"/{_page_path}",
                     f"legacy_{_page_path.replace('/', '_').replace('-', '_')}",
                     _legacy(_page_path))


# root: detect the visitor's language and 302 to the localized landing page
@app.route("/")
def detect():
    resp = redirect(f"/{detect_lang()}/", code=302)
    resp.headers["Vary"] = "Accept-Language"
    return resp


@app.route("/sitemap.xml")
def sitemap_xml():
    root = request.url_root.rstrip("/")
    # (path, changefreq, priority) — "" is the localized landing page
    entries = [
        ("", "daily", "1.0"),
        ("remover", "weekly", "0.9"),
        ("how-it-works", "weekly", "0.8"),
        ("features", "weekly", "0.8"),
        ("guides", "weekly", "0.8"),
        ("guides/remove-gemini-watermark-from-photos", "monthly", "0.8"),
        ("guides/remove-gemini-watermark-from-videos", "monthly", "0.8"),
        ("guides/what-is-the-gemini-watermark", "monthly", "0.7"),
        ("faq", "monthly", "0.8"),
        ("about", "monthly", "0.4"),
        ("privacy", "yearly", "0.3"),
        ("terms", "yearly", "0.3"),
    ]
    urls = []
    for path, freq, prio in entries:
        for l in LANGS:
            loc = f"{root}/{l}/" if not path else f"{root}/{l}/{path}"
            alts = {a: (f"{root}/{a}/" if not path else f"{root}/{a}/{path}")
                    for a in LANGS}
            urls.append({"loc": loc, "freq": freq, "prio": prio, "alts": alts})
    xml = render_template("sitemap.xml", urls=urls)
    return Response(xml, mimetype="application/xml")


@app.route("/healthz")
def healthz():
    return Response("ok", mimetype="text/plain")


@app.route("/robots.txt")
def robots_txt():
    root = request.url_root.rstrip("/")
    body = ("User-agent: *\n"
            "Allow: /\n\n"
            f"Sitemap: {root}/sitemap.xml\n")
    return Response(body, mimetype="text/plain")


@app.errorhandler(413)
def too_large(e):
    if request.path.startswith("/api/"):
        return jsonify({"error": "File too large (max 500 MB)."}), 413
    return render_template("404.html"), 413


@app.errorhandler(404)
def not_found(e):
    if request.path.startswith("/api/"):
        return jsonify({"error": "Not found"}), 404
    return render_template("404.html"), 404


def log(job, msg):
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    job["logs"].append(line)
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        print(line.encode("ascii", "replace").decode(), flush=True)


def _set_step(job, step_id, state):
    for s in job["steps"]:
        if s["id"] == step_id:
            s["state"] = state


def process_image(job, src, dst, box):
    log(job, "Loading image...")
    img = cv2.imread(src, cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError("Could not decode image. Try PNG/JPG/WebP.")
    h, w = img.shape[:2]
    box = clamp_box(box, w, h)
    log(job, f"Image {w}x{h}px. Selected region ({box[0]},{box[1]} {box[2]}x{box[3]}).")
    job["region"] = {"x": box[0], "y": box[1], "w": box[2], "h": box[3]}
    job["progress"] = 25

    log(job, "Building pixel-tight mask inside your selection...")
    mask = mask_from_box(img, box)
    job["region"]["masked_px"] = int((mask > 0).sum())
    log(job, f"Mask covers {int((mask > 0).sum())} px. Filling + matching texture...")
    job["progress"] = 70
    _set_step(job, "remove", "done")
    _set_step(job, "done", "active")

    # In-place fill: identical pixels to the old copy-based path, but peak
    # RAM is halved (no full-frame duplicate) — matters on 512 MB deploys.
    bbox = mask_bbox(mask)
    if bbox:
        clean_with_bbox(img, mask, bbox)
    out = img

    ext = os.path.splitext(dst)[1].lower()
    if ext in (".jpg", ".jpeg"):
        cv2.imwrite(dst, out, [cv2.IMWRITE_JPEG_QUALITY, 100])
    elif ext == ".webp":
        cv2.imwrite(dst, out, [cv2.IMWRITE_WEBP_QUALITY, 100])
    else:
        cv2.imwrite(dst, out)
    job["progress"] = 100
    _set_step(job, "done", "done")
    log(job, "Done - only the selected region was touched. Ready to download.")


def process_video(job, src, dst, box):
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    cap = cv2.VideoCapture(src)
    if not cap.isOpened():
        raise RuntimeError("Could not open video. Try MP4/MOV/WebM.")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    box = clamp_box(box, w, h)
    log(job, f"Video {w}x{h} @ {fps:.1f}fps, ~{total} frames.")
    log(job, f"Selected region ({box[0]},{box[1]} {box[2]}x{box[3]}) applies to all frames.")
    job["region"] = {"x": box[0], "y": box[1], "w": box[2], "h": box[3]}
    job["progress"] = 8

    # Union of tight masks from a few samples: covers the badge under
    # changing backgrounds while staying pixel-tight.
    log(job, "Sampling frames to lock the badge pixels...")
    idxs = sorted({0, max(0, total // 2), max(0, total - 1),
                   max(0, total // 4), max(0, 3 * total // 4)})
    union = np.zeros((h, w), dtype=np.uint8)
    for fidx in idxs[:5]:
        cap.set(cv2.CAP_PROP_POS_FRAMES, fidx)
        ok, fr = cap.read()
        if ok:
            union = cv2.bitwise_or(union, mask_from_box(fr, box))
    mask = cv2.dilate(union, np.ones((3, 3), np.uint8), iterations=1)
    job["region"]["masked_px"] = int((mask > 0).sum())
    job["bbox"] = mask_bbox(mask)  # fixed for the whole clip: no per-frame scan
    log(job, "Locked %d badge pixels. Cleaning + encoding..." % int((mask > 0).sum()))
    job["progress"] = 15
    _set_step(job, "remove", "active")

    # Single-pass: pipe cleaned frames straight into x264 (no temp file).
    # superfast preset: ~2x the encode speed of veryfast at the same CRF 17
    # (visually identical), tuned for small shared deploy CPUs.
    # RAM-bounded encoder: 2 threads + short lookahead. x264 otherwise sizes
    # its frame buffers from the host CPU count and lookahead (25+ frames),
    # which alone can exceed a 512 MB instance on HD/4K clips. Visual quality
    # at CRF 17 is unchanged; only the file size grows by a few percent.
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "warning",
           "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}",
           "-r", f"{fps:.3f}", "-i", "pipe:0",
           "-i", src,
           "-c:v", "libx264", "-preset", "superfast", "-crf", "17",
           "-threads", "2", "-x264-params", "rc-lookahead=10",
           "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "192k",
           "-map", "0:v:0", "-map", "1:a?", "-shortest", dst]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
    done = 0
    bbox = job.get("bbox")
    try:
        while True:
            ok, fr = cap.read()
            if not ok:
                break
            if bbox:
                clean_with_bbox(fr, mask, bbox)  # in place, no copy
            proc.stdin.write(fr.tobytes())
            done += 1
            if total > 0:
                job["progress"] = min(15 + int(75 * done / total), 92)
            if done % 120 == 0:
                log(job, f"Cleaned {done}/{total if total else '?'} frames...")
    except BrokenPipeError:
        pass
    finally:
        cap.release()
        try:
            proc.stdin.close()
        except BrokenPipeError:
            pass
        _, err = proc.communicate()
    _set_step(job, "remove", "done")
    _set_step(job, "encode", "done")
    if proc.returncode != 0 or not os.path.exists(dst) or os.path.getsize(dst) == 0:
        raise RuntimeError(f"Encode failed: {err[-600:].decode('utf-8', 'replace')}")
    job["progress"] = 100
    _set_step(job, "done", "done")
    log(job, f"Done - {done} frames, same {w}x{h}, original audio. Ready to download.")


def run_job(job_id):
    job = jobs[job_id]
    job["t0"] = time.time()
    try:
        job["stage"] = "processing"
        if job["kind"] == "image":
            process_image(job, job["src"], job["dst"], job["box"])
        else:
            # One video encode at a time: bounds RAM on small deploys.
            # Waiting jobs keep polling; their stage stays "processing".
            log(job, "Waiting for the encode slot...")
            with _video_slot:
                process_video(job, job["src"], job["dst"], job["box"])
        job["stage"] = "done"
        job["done"] = True
    except Exception as e:
        job["stage"] = "error"
        job["error"] = str(e)
        job["done"] = True
        log(job, f"ERROR: {e}")
        traceback.print_exc()
    finally:
        job["t_end"] = time.time()


@app.route("/api/upload", methods=["POST"])
def upload():
    _prune_jobs()
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "Empty filename"}), 400
    ext = os.path.splitext(f.filename)[1].lower()
    kind = "image" if ext in ALLOWED_IMG else ("video" if ext in ALLOWED_VID else None)
    if kind is None:
        return jsonify({"error": f"Unsupported file type {ext}. Use PNG/JPG/WebP or MP4/MOV/WebM."}), 400

    job_id = uuid.uuid4().hex[:10]
    safe = secure_filename(f.filename)
    src = os.path.join(UPLOAD_DIR, f"{job_id}_{safe}")
    out_name = f"{os.path.splitext(safe)[0]}_cleaned{'.mp4' if kind == 'video' else ext}"
    dst = os.path.join(OUTPUT_DIR, f"{job_id}_{out_name}")
    f.save(src)

    steps = [{"id": s, "label": STEP_LABELS[s],
              "state": "done" if s == "upload" else ("active" if s == "select" else "pending")}
             for s in (STEPS_VIDEO if kind == "video" else STEPS_IMAGE)]
    jobs[job_id] = {"stage": "awaiting_selection", "progress": 5, "logs": [], "steps": steps,
                    "done": False, "error": None, "result": out_name, "kind": kind,
                    "region": None, "box": None, "src": src, "dst": dst, "t0": time.time()}
    log(jobs[job_id], f"Upload complete: {safe} ({kind}). Draw a box around the watermark.")
    return jsonify({"job_id": job_id, "kind": kind})


@app.route("/api/process/<job_id>", methods=["POST"])
def process(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Unknown job"}), 404
    if job["stage"] == "processing":
        return jsonify({"error": "Already processing"}), 400
    data = request.get_json(force=True, silent=True) or {}
    box = data.get("box")
    if not box or len(box) != 4:
        return jsonify({"error": "Draw a box around the watermark first."}), 400
    try:
        job["box"] = [float(v) for v in box]
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid box coordinates."}), 400
    job["stage"] = "processing"
    job["done"] = False
    job["error"] = None
    _set_step(job, "select", "done")
    _set_step(job, "remove", "active")
    log(job, "Selection received. Starting removal...")
    t = threading.Thread(target=run_job, args=(job_id,), daemon=True)
    t.start()
    return jsonify({"ok": True})


@app.route("/api/preview/<job_id>", methods=["POST"])
def preview(job_id):
    """Crop the EXACT region a box selects (same file, same coords the
    removal will use) so the user can verify alignment first."""
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Unknown job"}), 404
    data = request.get_json(force=True, silent=True) or {}
    box = data.get("box")
    if not box or len(box) != 4:
        return jsonify({"error": "No box"}), 400
    try:
        if job["kind"] == "image":
            img = cv2.imread(job["src"], cv2.IMREAD_COLOR)
            if img is None:
                return jsonify({"error": "Cannot read image"}), 400
            h, w = img.shape[:2]
            x, y, bw, bh = clamp_box([float(v) for v in box], w, h)
            crop = img[y:y + bh, x:x + bw]
        else:
            cap = cv2.VideoCapture(job["src"])
            if not cap.isOpened():
                return jsonify({"error": "Cannot read video"}), 400
            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, total // 2))
            ok, fr = cap.read()
            cap.release()
            if not ok:
                return jsonify({"error": "Cannot read frame"}), 400
            x, y, bw, bh = clamp_box([float(v) for v in box], w, h)
            crop = fr[y:y + bh, x:x + bw]
        if crop.size == 0:
            return jsonify({"error": "Empty region"}), 400
        # upscale tiny selections so the thumbnail is inspectable
        scale = min(4.0, 320.0 / max(bw, bh)) if max(bw, bh) < 320 else 1.0
        if scale > 1.0:
            crop = cv2.resize(crop, None, fx=scale, fy=scale,
                              interpolation=cv2.INTER_NEAREST)
        ppath = os.path.join(OUTPUT_DIR, f"{job_id}_preview.png")
        cv2.imwrite(ppath, crop)
        job["preview"] = ppath
        return jsonify({"url": f"/api/preview_file/{job_id}"})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/preview_file/<job_id>")
def preview_file(job_id):
    job = jobs.get(job_id)
    if not job or not job.get("preview") or not os.path.exists(job["preview"]):
        return jsonify({"error": "No preview"}), 404
    return send_file(job["preview"], mimetype="image/png")


@app.route("/api/status/<job_id>")
def status(job_id):
    _prune_jobs()
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Unknown job"}), 404
    return jsonify({"stage": job["stage"], "progress": job["progress"],
                    "logs": job["logs"], "steps": job.get("steps", []),
                    "elapsed": round(time.time() - job.get("t0", time.time()), 1),
                    "done": job["done"],
                    "error": job["error"], "region": job["region"],
                    "result": job["result"] if job["done"] and not job["error"] else None})


@app.route("/api/download/<job_id>")
def download(job_id):
    job = jobs.get(job_id)
    if not job or not job["done"] or job["error"]:
        return jsonify({"error": "Result not ready"}), 404
    as_attach = request.args.get("view") != "1"
    return send_file(job["dst"], as_attachment=as_attach, download_name=job["result"])


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    host = os.environ.get("HOST", "0.0.0.0")
    print(f"Gemini Watermark Remover running at http://127.0.0.1:{port}")
    app.run(host=host, port=port, threaded=True)
