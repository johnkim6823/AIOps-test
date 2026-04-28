# Slim image — payment-api 시연용. 외부 의존성 없음.
FROM python:3.11-slim
WORKDIR /app
COPY app.py .
ENV PYTHONUNBUFFERED=1
USER 1000:1000
CMD ["python", "app.py"]
