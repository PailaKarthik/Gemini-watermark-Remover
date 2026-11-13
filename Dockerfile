FROM python:3.11-slim

WORKDIR /app

# Hugging Face Spaces routes Docker apps to this port; Render overrides PORT itself
ENV PORT=7860 \
    PYTHONUNBUFFERED=1

# opencv-python runtime libs (headless is not used so imread/imwrite behave identically to local)
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 7860
# Production server: 1 worker (video jobs run in bg threads) x 8 threads
# so progress polls stay snappy; 600s timeout so long encodes aren't killed.
CMD ["sh", "-c", "exec gunicorn app:app --bind 0.0.0.0:${PORT:-7860} --workers 1 --threads 8 --timeout 600"]
