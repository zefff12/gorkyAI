FROM python:3.13-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Семантический поиск: модель (~240 МБ на диске, ~700 МБ в памяти) скачивается при сборке.
# На хостинге с 512 МБ памяти собирайте с SEMANTIC_SEARCH=0 — останется лексический поиск.
ARG SEMANTIC_SEARCH=1
ENV SEMANTIC_SEARCH=${SEMANTIC_SEARCH} MODEL_CACHE=/app/models
RUN if [ "$SEMANTIC_SEARCH" = "1" ]; then \
      python -c "from fastembed import TextEmbedding; TextEmbedding('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2', cache_dir='/app/models')"; \
    fi
COPY app ./app
COPY static ./static
COPY data/voice_assistant_events_1000.csv ./data/
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
