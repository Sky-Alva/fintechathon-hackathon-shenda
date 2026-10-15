FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

ENV BANK_DB_PATH=/data/bank.sqlite3 \
    BANK_PORT=8000
RUN mkdir -p /data
EXPOSE 8000
CMD ["python", "-m", "bank.app"]
