FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml .
COPY src/ src/

RUN pip install --no-cache-dir .

RUN mkdir -p data/uploads

EXPOSE 8000

CMD ["uvicorn", "data_agent.main:app", "--host", "0.0.0.0", "--port", "8000"]
