FROM python:3.11-slim

WORKDIR /app

# Hugging Face Spaces routes Docker apps to this port; Render overrides PORT itself
# Single-threaded native math libs: numpy/OpenBLAS otherwise scale their
# thread pools to the host CPU count, wasting RAM on shared deploy CPUs.
# No effect on output pixels — only on thread counts.
ENV PORT=7860 \
    PYTHONUNBUFFERED=1 \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1

# opencv headless: identical imread/imwrite/inpaint/VideoCapture, no GUI libs,
# smaller image + less RAM (no libGL needed)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 7860
# Production server: 1 worker (video jobs run in bg threads, 1 encode at a
# time) x 2 threads — polls/uploads are I/O-bound so 2 is plenty on 512 MB;
# 900s timeout so long encodes aren't killed.
CMD ["sh", "-c", "exec gunicorn app:app --bind 0.0.0.0:${PORT:-7860} --workers 1 --threads 2 --timeout 900"]
