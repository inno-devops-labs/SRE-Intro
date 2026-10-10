FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN groupadd --system app && \
    useradd --system --gid app --home-dir /app --no-create-home app

COPY main.py .

ENV PYTHONDONTWRITEBYTECODE=1

USER app

EXPOSE 8080
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
