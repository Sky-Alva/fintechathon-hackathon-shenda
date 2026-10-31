ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE}

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
# pip reads PIP_INDEX_URL by itself.
ARG PIP_INDEX_URL=https://pypi.org/simple
RUN pip install --no-cache-dir .

ENV BANK_URL=http://bank:8000 \
    AGENT_PORT=8001
EXPOSE 8001
CMD ["python", "-m", "agent.app"]
