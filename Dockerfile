FROM python:3.12-slim

WORKDIR /app

# системные зависимости
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# сначала зависимости (кешируются)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# потом код
COPY . .

ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "app.main"]
