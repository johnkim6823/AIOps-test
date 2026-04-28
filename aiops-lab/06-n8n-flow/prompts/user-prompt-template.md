# 자리 표시자(`{{ }}`)는 n8n Code 노드가 알람·kubectl 결과로 치환한다.
# 이 파일은 사람이 읽기 위한 템플릿. 실제 LLM 입력은 한 줄 JSON.

[ALERT]
{{alert_json}}

[POD]
namespace: {{namespace}}
name:      {{pod_name}}
container: {{container_name}}

[POD describe (truncated)]
{{kubectl_describe}}

[Container logs — previous instance, last 80 lines]
{{kubectl_logs_previous}}

[Recent events filtered to this pod]
{{kubectl_events}}
