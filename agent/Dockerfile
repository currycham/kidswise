FROM python:3.12-slim
WORKDIR /app
ARG PIP_INDEX_URL=https://mirrors.cloud.tencent.com/pypi/simple
COPY requirements.txt .
RUN pip install --no-cache-dir -i "$PIP_INDEX_URL" -r requirements.txt
COPY waywise_agent ./waywise_agent
COPY data ./data
EXPOSE 8080
CMD ["uvicorn", "waywise_agent.app:app", "--host", "0.0.0.0", "--port", "8080"]
