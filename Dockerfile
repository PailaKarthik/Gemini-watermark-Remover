FROM python:3.11-slim

WORKDIR /app

# Hugging Face Spaces routes Docker apps to this port; Render overrides PORT itself
ENV PORT=7860 \
    PYTHONUNBUFFERED=1

# opencv headless: identical imread/imwrite/inpaint/VideoCapture, no GUI libs,
# smaller image + less RAM (no libGL needed)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 7860
# Static host: all watermark removal runs in the visitor's browser, so the
# server needs no compute power — 1 worker x 2 threads is plenty.
CMD ["sh", "-c", "exec gunicorn app:app --bind 0.0.0.0:${PORT:-7860} --workers 1 --threads 2 --timeout 900"]
