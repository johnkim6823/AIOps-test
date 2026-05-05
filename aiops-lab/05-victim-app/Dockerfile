# Slim image — payment-api 시연용. 외부 의존성 없음.
FROM python:3.11-slim

WORKDIR /app

# Read-only root filesystem 환경에서 안전 동작:
#   - .pyc 디스크 쓰기 비활성화
#   - 표준 출력 라인버퍼링 (Promtail 이 늦지 않게 받음)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY app.py .

# non-root user (gid:uid 매칭은 deployment 의 runAsUser 와 일치)
RUN useradd -u 1000 -m payment && chown payment:payment /app
USER 1000:1000

EXPOSE 8080
CMD ["python", "app.py"]
