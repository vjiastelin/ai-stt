# ai-service: Prometheus-метрики

`GET /metrics` на порту сервиса (8080) отдаёт метрики в text exposition format.
Зависимостей на инфраструктуру нет: gauges очереди пересчитываются из SQLite на
каждом scrape, счётчики/гистограммы обновляются worker'ом по ходу pipeline.

Пример scrape-конфига:

```yaml
scrape_configs:
  - job_name: ai-service
    scrape_interval: 30s
    static_configs:
      - targets: ["ai-service-host:8080"]
```

## Метрики

| Метрика | Тип | Описание |
|---|---|---|
| `ai_service_queue_jobs{status}` | gauge | Число задач в каждом состоянии (`queued/processing/delivering/done/failed`). Пересчитывается на scrape. |
| `ai_service_oldest_queued_age_seconds` | gauge | Возраст старейшей `queued`-задачи; 0 при пустой очереди. |
| `ai_service_jobs_enqueued_total` | counter | Принятые запросы `/requestTranscription` (включая повторную постановку failed). |
| `ai_service_jobs_resolved_total{status}` | counter | Задачи, достигшие терминального состояния (`done`/`failed`). |
| `ai_service_job_retries_total{kind}` | counter | Ретраи: `infrastructure` (зависимость недоступна, не считается против задачи) / `permanent` (плохой вход, инкрементирует attempts). |
| `ai_service_stage_duration_seconds{stage}` | histogram | Длительность успешных этапов: `download`, `transcribe`, `summarize`, `callback`. Ошибки в гистограмму не попадают. |
| `ai_service_stage_errors_total{stage,kind}` | counter | Ошибки этапов по таксономии. |
| `ai_service_job_end_to_end_seconds` | histogram | От постановки в очередь до успешной доставки в BPM. |
| `ai_service_transcribe_rtf` | histogram | Real-time factor: время транскрипции ÷ длительность аудио. Чистая метрика GPU, не зависит от длины звонков. |
| `ai_service_audio_seconds_total` | counter | Суммарные секунды обработанного аудио. |

## Алерты (PromQL)

Пороги основаны на нагрузочном тестировании (пик 100 звонков/час, среднее время
обработки 16 с/задача, ёмкость ~228/час ⇒ ~44% утилизации в пик):

```yaml
groups:
  - name: ai-service
    rules:
      # Очередь растёт — ёмкость исчерпана или whisper недоступен
      - alert: QueueBacklog
        expr: ai_service_queue_jobs{status="queued"} > 20
        for: 15m

      # Задача стоит в очереди дольше 10 минут в рабочие часы — worker застрял или перегруз
      - alert: StaleQueuedJob
        expr: ai_service_oldest_queued_age_seconds > 600
        for: 5m

      # SLO: p90 от запроса до доставки в BPM > 5 минут
      - alert: SlowEndToEnd
        expr: |
          histogram_quantile(0.9,
            rate(ai_service_job_end_to_end_seconds_bucket[15m])) > 300
        for: 15m

      # Среднее время транскрипции > 25s ⇒ утилизация в пик уйдёт за 70% —
      # сигнал переходить на turbo-модель или ускорять GPU
      - alert: TranscribeSlowdown
        expr: |
          rate(ai_service_stage_duration_seconds_sum{stage="transcribe"}[1h])
            / rate(ai_service_stage_duration_seconds_count{stage="transcribe"}[1h]) > 25
        for: 1h

      # Доля failed за сутки > 1% — битые записи или системная проблема
      - alert: HighFailureRate
        expr: |
          increase(ai_service_jobs_resolved_total{status="failed"}[24h])
            / clamp_min(increase(ai_service_jobs_enqueued_total[24h]), 1) > 0.01

      # BPM не принимает результаты — delivering копится
      - alert: CallbackStuck
        expr: ai_service_queue_jobs{status="delivering"} > 5
        for: 10m

      # Сервис не отвечает на scrape
      - alert: AiServiceDown
        expr: up{job="ai-service"} == 0
        for: 2m
```

Замечания:

- При **ночном выключении GPU** (whisper недоступен по расписанию) алерты
  `QueueBacklog`/`StaleQueuedJob` в ночном окне ожидаемы — добавьте
  time-based inhibition или условие на рабочие часы.
- Рост `ai_service_job_retries_total{kind="infrastructure"}` без алертов выше —
  флаппинг whisper/сети/S3: повод посмотреть логи до того, как это станет очередью.
- Тренд `rate(ai_service_jobs_enqueued_total[1h])` к ~160/час в пике (~70% ёмкости) —
  ранний сигнал планировать масштабирование.
