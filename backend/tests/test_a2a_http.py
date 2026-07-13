from fastapi import FastAPI
from fastapi.testclient import TestClient

from riskagent.a2a import (
    RuntimeDirectory,
    TeamRuntime,
    build_dynamic_expert_a2a_routes,
    build_expert_a2a_routes,
)
from riskagent.api.main import create_app


def _rpc(method: str, params: dict, request_id: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": params,
    }


def test_a2a_http_routes_bridge_mailbox_and_durable_task_store(tmp_path, monkeypatch):
    monkeypatch.setenv("USE_DATABASE", "0")
    runtime = TeamRuntime("run-http", root_dir=tmp_path)
    calls = {"count": 0}

    def handler(payload, peer):
        calls["count"] += 1
        return {"answer": payload["value"] * 2}

    runtime.start_expert("legal_agent", handler, role="Legal analyst", capabilities=["legal"])
    assert runtime.wait_until_idle(["legal_agent"], timeout=2.0)

    with TestClient(create_app()) as client:
        card_response = client.get("/a2a/legal_agent/.well-known/agent-card.json")
        assert card_response.status_code == 200
        assert card_response.json()["name"] == "legal_agent"
        assert card_response.json()["defaultInputModes"] == ["application/json"]
        assert card_response.json()["defaultOutputModes"] == ["application/json"]

        missing_version = client.post(
            "/a2a/legal_agent",
            json=_rpc("GetTask", {"id": "missing"}, "missing-version"),
        )
        assert missing_version.status_code == 200
        assert missing_version.json()["error"]["code"] == -32009

        send_response = client.post(
            "/a2a/legal_agent",
            headers={"A2A-Version": "1.0"},
            json=_rpc(
                "SendMessage",
                {
                    "message": {
                        "messageId": "external-message-1",
                        "contextId": "run-http",
                        "role": "ROLE_USER",
                        "parts": [{"data": {"value": 21}}],
                    }
                },
                "send-1",
            ),
        )
        assert send_response.status_code == 200
        sent_task = send_response.json()["result"]["task"]
        task_id = sent_task["id"]
        assert task_id
        assert sent_task["status"]["state"] == "TASK_STATE_COMPLETED"
        assert sent_task["artifacts"][0]["parts"][0]["data"] == {
            "status": "completed",
            "result": {"answer": 42.0},
        }
        assert runtime.mailbox.inbox_path("legal_agent").stat().st_size > 0

        get_response = client.post(
            "/a2a/legal_agent",
            headers={"A2A-Version": "1.0"},
            json=_rpc("GetTask", {"id": task_id, "historyLength": 2}, "get-1"),
        )
        assert get_response.status_code == 200
        assert get_response.json()["result"]["status"]["state"] == "TASK_STATE_COMPLETED"
        assert len(get_response.json()["result"]["history"]) == 2

        list_response = client.post(
            "/a2a/legal_agent",
            headers={"A2A-Version": "1.0"},
            json=_rpc("ListTasks", {"contextId": "run-http"}, "list-1"),
        )
        assert list_response.status_code == 200
        assert [task["id"] for task in list_response.json()["result"]["tasks"]] == [
            task_id
        ]

        retry_response = client.post(
            "/a2a/legal_agent",
            headers={"A2A-Version": "1.0"},
            json=_rpc(
                "SendMessage",
                {
                    "message": {
                        "messageId": "external-message-1",
                        "contextId": "run-http",
                        "role": "ROLE_USER",
                        "parts": [{"data": {"value": 21}}],
                    }
                },
                "send-retry",
            ),
        )
        assert retry_response.status_code == 200
        assert retry_response.json()["result"]["task"]["id"] == task_id
        assert calls["count"] == 1

        missing_response = client.post(
            "/a2a/legal_agent",
            headers={"A2A-Version": "1.0"},
            json=_rpc(
                "SendMessage",
                {
                    "message": {
                        "messageId": "external-message-with-missing-task",
                        "contextId": "run-http",
                        "taskId": "client-invented-task",
                        "role": "ROLE_USER",
                        "parts": [{"data": {"value": 1}}],
                    }
                },
                "send-missing-task",
            ),
        )
        assert missing_response.status_code == 200
        assert "error" in missing_response.json()

    runtime.shutdown_all(timeout=3.0)


def test_a2a_http_bridge_discovers_worker_runtime_from_shared_directory(tmp_path):
    runtime = TeamRuntime("run-shared-worker", root_dir=tmp_path)

    def handler(payload, peer):
        return {"worker_process_result": payload["value"] + 1}

    runtime.start_expert("legal_agent", handler)
    assert runtime.wait_until_idle(["legal_agent"], timeout=2.0)

    detached_directory = RuntimeDirectory()
    app = FastAPI()
    app.router.routes.extend(
        build_expert_a2a_routes(
            [{"agent_name": "legal_agent", "capabilities": ["legal"]}],
            public_base_url="http://testserver",
            directory=detached_directory,
            runtime_root=tmp_path,
            send_timeout=3.0,
        )
    )
    with TestClient(app) as client:
        response = client.post(
            "/a2a/legal_agent",
            headers={"A2A-Version": "1.0"},
            json=_rpc(
                "SendMessage",
                {
                    "message": {
                        "messageId": "cross-process-message",
                        "contextId": "run-shared-worker",
                        "role": "ROLE_USER",
                        "parts": [{"data": {"value": 6}}],
                    }
                },
                "cross-process-send",
            ),
        )

        assert response.status_code == 200
        task = response.json()["result"]["task"]
        assert task["status"]["state"] == "TASK_STATE_COMPLETED"
        assert task["artifacts"][0]["parts"][0]["data"]["result"] == {
            "worker_process_result": 7.0
        }

    runtime.shutdown_all(timeout=3.0)


def test_a2a_routes_discover_expert_added_after_app_start(tmp_path):
    catalog: list[dict[str, object]] = []
    app = FastAPI()
    app.router.routes.extend(
        build_dynamic_expert_a2a_routes(
            lambda: catalog,
            public_base_url="http://testserver",
            runtime_root=tmp_path,
            send_timeout=3.0,
        )
    )

    with TestClient(app) as client:
        assert (
            client.get("/a2a/new_expert/.well-known/agent-card.json").status_code
            == 404
        )
        catalog.append(
            {
                "agent_name": "new_expert",
                "title": "Newly registered expert",
                "capabilities": ["new capability"],
            }
        )
        card = client.get("/a2a/new_expert/.well-known/agent-card.json")
        assert card.status_code == 200
        assert card.json()["name"] == "new_expert"

        runtime = TeamRuntime("run-dynamic-card", root_dir=tmp_path)
        runtime.start_expert("new_expert", lambda payload, _peer: {"seen": payload})
        assert runtime.wait_until_idle(["new_expert"], timeout=2.0)
        response = client.post(
            "/a2a/new_expert",
            headers={"A2A-Version": "1.0"},
            json=_rpc(
                "SendMessage",
                {
                    "message": {
                        "messageId": "dynamic-expert-message",
                        "contextId": "run-dynamic-card",
                        "role": "ROLE_USER",
                        "parts": [{"data": {"value": 9}}],
                    }
                },
                "dynamic-send",
            ),
        )
        assert response.status_code == 200
        assert response.json()["result"]["task"]["status"]["state"] == (
            "TASK_STATE_COMPLETED"
        )
        runtime.shutdown_all(timeout=3.0)
        catalog.clear()
        assert (
            client.get("/a2a/new_expert/.well-known/agent-card.json").status_code
            == 404
        )
