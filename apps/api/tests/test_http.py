from fastapi.testclient import TestClient

from medipet.delivery.http import app

client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_chat_uses_ai_sdk_ui_stream_protocol() -> None:
    response = client.post(
        "/v1/chat/turns",
        json={
            "id": "conversation-1",
            "messages": [
                {
                    "id": "message-1",
                    "role": "user",
                    "parts": [{"type": "text", "text": "我头痛，应该去哪个科室？"}],
                }
            ],
        },
    )

    assert response.status_code == 200
    assert response.headers["x-vercel-ai-ui-message-stream"] == "v1"
    assert '"type":"start"' in response.text
    assert '"type":"text-delta"' in response.text
    assert '"type":"data-department-candidates"' in response.text
    assert "Thought" not in response.text
    assert response.text.rstrip().endswith("data: [DONE]")
