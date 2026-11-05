# ITSM RAG server (multi-arch: arm64 GB10 / x86_64). Needs an NVIDIA GPU at run time (--gpus all);
# the CUDA libraries ship inside the torch wheel, so a slim base is enough.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv
COPY requirements.txt .
RUN pip install --extra-index-url https://download.pytorch.org/whl/cu130 -r requirements.txt
COPY app/ app/
# Ticket archive, ServiceNow file, index cache and HF cache are mounted (see ../docker-compose.yml).
ENV ITSM_DATA=/data/tickets ITSM_SNOW=/data/servicenow.json ITSM_CACHE=/cache/index HF_HOME=/cache/hf \
    TRANSFORMERS_VERBOSITY=error
EXPOSE 8030
HEALTHCHECK --interval=30s --timeout=5s --start-period=600s \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8030/health')" || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8030", "--log-level", "info"]
