FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

COPY service/requirements.compose.lock /tmp/requirements.lock
RUN pip install --no-cache-dir --no-deps -r /tmp/requirements.lock && pip check

COPY alembic.ini ./
COPY service ./service
COPY demo ./demo

USER 10001:10001
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "service.api:create_app", "--factory", "--host", "0.0.0.0"]
