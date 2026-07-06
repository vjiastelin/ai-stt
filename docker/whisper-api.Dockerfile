FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04

RUN apt-get update \
 && apt-get install -y --no-install-recommends python3.11 python3.11-venv \
 && rm -rf /var/lib/apt/lists/*
ENV VIRTUAL_ENV=/opt/venv PATH=/opt/venv/bin:$PATH
RUN python3.11 -m venv /opt/venv

WORKDIR /app
COPY pyproject.toml ./
COPY ai_service ./ai_service
COPY whisper_api ./whisper_api
RUN pip install --no-cache-dir .[api]

ENV HF_HOME=/cache/huggingface
EXPOSE 8000
CMD ["python", "-m", "whisper_api"]
