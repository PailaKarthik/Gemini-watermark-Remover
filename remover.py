"""
Manual-region watermark removal (v3).

No auto-detection: the user drags a box around the watermark.
Only pixels inside that box are ever touched - everything else is
copied through untouched. Speed comes from crop-only inpainting
(the edited region is tiny) and texture grain computed on the crop.
"""
import cv2
import numpy as np


def clamp_box(box, w: int, h: int, min_size: int = 8):
    """Validate + clamp a user box to frame bounds. Raises ValueError."""
    try:
        x, y, bw, bh = (int(round(float(v))) for v in box)
    except (TypeError, ValueError):
        raise ValueError("Invalid selection box.")
    if bw < min_size or bh < min_size:
        raise ValueError(f"Selection too small (min {min_size}x{min_size}px).")
    x = max(0, min(x, w - 1))
    y = max(0, min(y, h - 1))
    bw = max(min_size, min(bw, w - x))
    bh = max(min_size, min(bh, h - y))
    return x, y, bw, bh


def mask_from_box(bgr: np.ndarray, box) -> np.ndarray:
    """Pixel-tight mask of the bright watermark pixels inside the box.

    Only genuinely bright badge pixels get masked (1px fringe), so the
    fill area stays minimal. If the box has no distinct bright pixels
    (dim/translucent badge), falls back to the box core.
    """
    h, w = bgr.shape[:2]
    x, y, bw, bh = clamp_box(box, w, h)
    pad = 6
    x0, y0 = max(x - pad, 0), max(y - pad, 0)
    x1, y1 = min(x + bw + pad, w), min(y + bh + pad, h)
    crop = bgr[y0:y1, x0:x1]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY).astype(np.float32)
    mean, std = float(gray.mean()), float(gray.std())
    thresh = max(150.0, mean + 1.5 * std)
    _, m = cv2.threshold(gray.astype(np.uint8), int(thresh), 255, cv2.THRESH_BINARY)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8), iterations=1)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m, 8)
    keep = np.zeros_like(m)
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] >= 12:
            keep[lab == i] = 255
    mask = np.zeros((h, w), np.uint8)
    mask[y0:y1, x0:x1] = keep
    if int((mask > 0).sum()) < int(0.08 * bw * bh):
        cv2.rectangle(mask, (x + 1, y + 1), (x + bw - 1, y + bh - 1), 255, -1)
    return cv2.dilate(mask, np.ones((3, 3), np.uint8), iterations=1)


def _grain_crop(orig: np.ndarray, filled: np.ndarray, m: np.ndarray) -> np.ndarray:
    """Cheap grain match on the crop: noise std sampled from the
    surrounding ring so the fill matches local texture (no smooth look)."""
    ring = cv2.dilate(m, np.ones((9, 9), np.uint8), iterations=1)
    ring = cv2.subtract(ring, cv2.dilate(m, np.ones((5, 5), np.uint8), iterations=1))
    if int((ring > 0).sum()) < 24:
        return filled
    out = filled.astype(np.float32)
    rng = np.random.default_rng(7)
    feather = cv2.GaussianBlur((m > 0).astype(np.float32), (3, 3), 0)
    for c in range(3):
        ch = orig[:, :, c].astype(np.float32)
        std = float(ch[ring > 0].std())
        if std < 0.7:
            continue
        noise = rng.normal(0, min(std, 9.0), ch.shape).astype(np.float32)
        out[:, :, c] = filled[:, :, c].astype(np.float32) + noise * feather
    return np.clip(out, 0, 255).astype(np.uint8)


def mask_bbox(mask: np.ndarray, pad: int = 10):
    """Tight crop rect around all masked pixels. Computed ONCE per video
    (the union mask never changes) instead of re-scanning every frame."""
    ys, xs = np.where(mask > 0)
    if len(xs) == 0:
        return None
    h, w = mask.shape[:2]
    x0, y0 = max(int(xs.min()) - pad, 0), max(int(ys.min()) - pad, 0)
    x1, y1 = min(int(xs.max()) + pad + 1, w), min(int(ys.max()) + pad + 1, h)
    return (x0, y0, x1, y1)


def clean_with_bbox(bgr: np.ndarray, mask: np.ndarray, bbox) -> np.ndarray:
    """Same fill as clean_with_mask but reuses a precomputed bbox and edits
    the frame buffer in place — no per-frame full-image scan or copy."""
    x0, y0, x1, y1 = bbox
    crop = bgr[y0:y1, x0:x1]
    m = mask[y0:y1, x0:x1]
    filled = cv2.inpaint(crop, m, 2, cv2.INPAINT_NS)
    # second pass on any still-bright fringe inside the mask
    inner = cv2.erode(m, np.ones((3, 3), np.uint8), iterations=1)
    if int((inner > 0).sum()) > 0:
        g = cv2.cvtColor(filled, cv2.COLOR_BGR2GRAY)
        fringe = np.zeros_like(m)
        sel = inner > 0
        fringe[sel] = np.where(g[sel] > 215, 255, 0).astype(np.uint8)
        if int((fringe > 0).sum()) > 0:
            filled = cv2.inpaint(filled, fringe, 2, cv2.INPAINT_TELEA)
    filled = _grain_crop(crop, filled, m)
    bgr[y0:y1, x0:x1] = filled
    return bgr


def clean_with_mask(bgr: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Fill masked pixels. Only the masked bounding region is processed;
    all other pixels are returned untouched."""
    ys, xs = np.where(mask > 0)
    if len(xs) == 0:
        return bgr.copy()
    pad = 10
    h, w = bgr.shape[:2]
    x0, y0 = max(int(xs.min()) - pad, 0), max(int(ys.min()) - pad, 0)
    x1, y1 = min(int(xs.max()) + pad + 1, w), min(int(ys.max()) + pad + 1, h)
    crop = bgr[y0:y1, x0:x1]
    m = mask[y0:y1, x0:x1]
    filled = cv2.inpaint(crop, m, 2, cv2.INPAINT_NS)
    # second pass on any still-bright fringe inside the mask
    inner = cv2.erode(m, np.ones((3, 3), np.uint8), iterations=1)
    if int((inner > 0).sum()) > 0:
        g = cv2.cvtColor(filled, cv2.COLOR_BGR2GRAY)
        fringe = np.zeros_like(m)
        sel = inner > 0
        fringe[sel] = np.where(g[sel] > 215, 255, 0).astype(np.uint8)
        if int((fringe > 0).sum()) > 0:
            filled = cv2.inpaint(filled, fringe, 2, cv2.INPAINT_TELEA)
    filled = _grain_crop(crop, filled, m)
    out = bgr.copy()
    out[y0:y1, x0:x1] = filled
    return out


def remove_region(bgr: np.ndarray, box):
    """Clean a user-selected box. Returns (cleaned_image, mask)."""
    mask = mask_from_box(bgr, box)
    return clean_with_mask(bgr, mask), mask
