FROM python:3.11-slim

# System deps needed by pypdf/lxml/torch runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
# CUDA-enabled torch matching the RTX 5090 (Blackwell, sm_120) — needs the
# cu128 wheel index and torch>=2.7 for Blackwell support (2.4.1 predates it).
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cu128
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
