FROM python:3.11-slim

WORKDIR /app
COPY pyproject.toml ./
COPY ai_service ./ai_service
COPY whisper_api ./whisper_api
RUN pip install --no-cache-dir .[service]

VOLUME /data
EXPOSE 8080
CMD ["python", "-m", "ai_service"]
