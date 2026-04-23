# Slack Setup Guide — Incoming Webhook + Interactivity

AIOps 랩의 Phase 3 / Phase 6에서 사용할 Slack 환경을 준비합니다.
약 15~20분 소요. Phase 0/1 진행과 병행 가능.

---

## 1. 개인 워크스페이스 준비

기존 회사 워크스페이스를 쓰지 말고 테스트 전용 워크스페이스를 새로 만듭니다.

1. <https://slack.com/get-started#/createnew> 에서 `+ Create a new workspace`
2. 이메일 인증 후 워크스페이스 이름 지정 (예: `aiops-lab`)
3. 기본 채널 `#alerts` 를 생성 (웹훅·승인 메시지 수신용)

---

## 2. Slack App 생성

1. <https://api.slack.com/apps> → `Create New App` → `From scratch`
2. App Name: `aiops-lab-bot`, Workspace: 위에서 만든 것
3. `Create App` 클릭

---

## 3. Incoming Webhook 활성화 (진단 결과·적용 완료 리포트용)

1. 좌측 메뉴 `Incoming Webhooks` → `Activate Incoming Webhooks` ON
2. 하단 `Add New Webhook to Workspace` 클릭
3. 채널 `#alerts` 선택 → `Allow`
4. 생성된 URL(예: `https://hooks.slack.com/services/T000.../B000.../XXXXX`) 복사
5. 나중에 `aiops-lab/03-workflow/.env` 에 다음 줄로 저장 예정:
   ```
   SLACK_WEBHOOK_URL=https://hooks.slack.com/services/.../.../...
   ```

---

## 4. Bot Token Scopes + Interactivity (승인 버튼용)

승인 버튼(Block Kit action)은 Incoming Webhook만으로는 동작하지 않습니다. Bot Token과 Interactivity Request URL이 필요합니다.

### 4-1. OAuth Scopes

1. 좌측 `OAuth & Permissions` → `Scopes` → `Bot Token Scopes`
2. 다음 스코프 추가:
   - `chat:write`       (봇이 메시지 게시)
   - `chat:write.public` (봇이 초대되지 않은 공개 채널에도 게시)
   - `channels:history`  (E2E 테스트에서 메시지 확인용)
3. 상단 `Install to Workspace` → 권한 동의 → `Bot User OAuth Token` (xoxb-...) 복사

### 4-2. Interactivity & Shortcuts

Slack은 사용자가 버튼을 누르면 지정된 HTTPS URL로 POST를 보냅니다. 로컬 n8n 을 외부에 노출하려면 터널이 필요합니다.

**ngrok 설치 (무료 계정으로 충분)**
```bash
curl -s https://ngrok-agent.s3.amazonaws.com/ngrok.asc | sudo tee /etc/apt/trusted.gpg.d/ngrok.asc >/dev/null
echo "deb https://ngrok-agent.s3.amazonaws.com buster main" | sudo tee /etc/apt/sources.list.d/ngrok.list
sudo apt-get update && sudo apt-get install -y ngrok
ngrok config add-authtoken <your-token-from-ngrok.com>
```

**n8n Webhook을 외부로 노출 (Phase 3 이후 실행)**
```bash
ngrok http 5678
# Forwarding https://abcd-1234.ngrok-free.app -> http://localhost:5678
```

위에서 받은 `https://abcd-1234.ngrok-free.app` 을 메모. Phase 6에서 사용.

**Slack Interactivity URL 등록**
1. 좌측 `Interactivity & Shortcuts` → `Interactivity` ON
2. `Request URL` 에 다음 입력:
   ```
   https://abcd-1234.ngrok-free.app/webhook/slack-approve
   ```
   (경로 `/webhook/slack-approve` 는 Phase 6 n8n 워크플로우의 Webhook 노드 path와 일치시킴)
3. `Save Changes`

> ngrok 무료 플랜은 세션마다 URL이 바뀝니다. 재시작할 때마다 Slack Request URL을 다시 등록해야 합니다. 발표 직전에 한 번 띄우고 그대로 유지하는 걸 권장.

---

## 5. Bot을 채널에 초대

```
#alerts 채널에서:
/invite @aiops-lab-bot
```

---

## 6. 수집할 값 체크리스트

Phase 3의 `.env` 파일에 들어갈 값들입니다. 지금 메모만 해두세요.

| 변수 | 값 출처 | 예시 |
|------|--------|------|
| `SLACK_WEBHOOK_URL`       | 3단계에서 복사         | `https://hooks.slack.com/services/T.../B.../...` |
| `SLACK_BOT_TOKEN`         | 4-1단계에서 복사       | `xoxb-...`                                        |
| `SLACK_CHANNEL`           | 알림 받을 채널 이름     | `#alerts`                                         |
| `SLACK_SIGNING_SECRET`    | App 설정 `Basic Information` → `App Credentials` | `abcdef...` |
| `NGROK_PUBLIC_URL`        | 4-2단계 ngrok 포워딩   | `https://abcd-1234.ngrok-free.app`                |

---

## 7. 빠른 검증 (선택)

Webhook만 먼저 테스트:
```bash
curl -X POST -H 'Content-Type: application/json' \
  --data '{"text":"AIOps lab webhook test ok"}' \
  "$SLACK_WEBHOOK_URL"
```
`#alerts` 채널에 메시지가 뜨면 성공.

완료되면 저에게 알려주세요. Phase 1 진행하면서 이 값들을 `.env` 에 투입합니다.
