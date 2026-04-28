# 04a-rl-stub: heuristic FastAPI for n8n /decide
# Slim — torch / gymnasium 등 RL 의존성 없음. Phase 4b (실제 학습 모델) 로 교체 시
# 컨테이너 통째로 대체하면 된다.

FROM python:3.11-slim

WORKDIR /app

# 의존성을 먼저 복사 → 코드만 변경되면 layer 재사용
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

EXPOSE 8000

# uvicorn 직행 (uvicorn[standard] 의 uvloop/httptools 자동 사용)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
