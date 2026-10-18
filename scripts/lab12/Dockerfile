FROM python:3.13-slim
WORKDIR /work
COPY requirements-lab9.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt httpx==0.28.1
CMD ["sleep", "infinity"]
