ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE}

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
# pip reads PIP_INDEX_URL by itself.
ARG PIP_INDEX_URL=https://pypi.org/simple
RUN pip install --no-cache-dir .

ENV BANK_DB_PATH=/data/bank.sqlite3 \
    BANK_PORT=8000
RUN mkdir -p /data
EXPOSE 8000
CMD ["python", "-m", "bank.app"]
