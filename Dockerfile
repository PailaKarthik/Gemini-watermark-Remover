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
CMD ["python", "app.py"]
