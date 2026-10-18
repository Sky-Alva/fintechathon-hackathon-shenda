FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

ENV BANK_URL=http://bank:8000 \
    AGENT_PORT=8001
EXPOSE 8001
CMD ["python", "-m", "agent.app"]
