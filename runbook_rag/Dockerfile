FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir fastapi==0.141.1 uvicorn==0.52.1 httpx==0.28.1
COPY runbook_rag/ runbook_rag/
ENV RUNBOOK_REQUIRE_RELEASE=1
USER 10001
CMD ["sh", "-c", "exec python -m uvicorn runbook_rag.api:app --host 0.0.0.0 --port ${PORT:-8000} --no-access-log"]
