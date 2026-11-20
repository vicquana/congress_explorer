# Congress Explorer web app (Streamlit + DuckDB).
#
# The corpus is NOT baked into the image (it is 3.7 GB). Either mount it and set CONGRESS_DATA_DIR, or let the app
# download the pinned Hugging Face dataset snapshot into HF_HOME on first use. Keep /data on a persistent volume,
# otherwise the download repeats on every restart. See DEPLOY.md.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/data/huggingface \
    CONGRESS_SEARCH_CACHE_DIR=/data/search_cache \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

WORKDIR /app

# Pinned run-time dependencies only (no torch/CUDA); regenerate with the commands at the top of requirements-app.txt.
COPY requirements-app.txt ./
RUN pip install -r requirements-app.txt

COPY app ./app
COPY manifest.json ./manifest.json

RUN useradd --create-home --uid 1000 appuser && mkdir -p /data && chown -R appuser:appuser /data /app
USER appuser

EXPOSE 8501

# Streamlit answers this before any corpus is loaded; it shows the server is up, not that the data is present.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=4).status == 200 else 1)"

# CORS and XSRF protection stay at Streamlit's defaults (on). Behind a TLS-terminating proxy that is what you want.
# toolbarMode=viewer hides the developer toolbar (the "Deploy" button) from visitors.
CMD ["streamlit", "run", "app/app.py", "--server.port=8501", "--server.address=0.0.0.0", "--server.headless=true", "--client.toolbarMode=viewer"]
