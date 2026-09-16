FROM python:3.12-slim-bookworm AS base
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 PYTHONPATH=/app

FROM base AS dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Chroma computes the default embeddings in the API client, including HTTP mode.
RUN python -c "from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import ONNXMiniLM_L6_V2; ONNXMiniLM_L6_V2()(['MediPet'])"

FROM base AS production
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-chi-sim \
    && rm -rf /var/lib/apt/lists/*
RUN useradd -m -u 1000 medipet
COPY --from=dependencies /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=dependencies /usr/local/bin /usr/local/bin
COPY --from=dependencies --chown=medipet:medipet /root/.cache/chroma /home/medipet/.cache/chroma
COPY --chown=medipet:medipet . .
RUN mkdir -p /app/data/chroma /app/data/eval /app/logs && chown -R medipet:medipet /app/data /app/logs
USER medipet
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=90s --retries=5 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=4).read()"
CMD ["python","-m","uvicorn","api.main:app","--host","0.0.0.0","--port","8000"]

