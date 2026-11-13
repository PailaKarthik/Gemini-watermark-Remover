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
# Production server: 1 worker (video jobs run in bg threads, 1 encode at a
# time) x 4 threads so progress polls stay snappy on 512 MB instances;
# 900s timeout so long encodes aren't killed.
CMD ["sh", "-c", "exec gunicorn app:app --bind 0.0.0.0:${PORT:-7860} --workers 1 --threads 4 --timeout 900"]
