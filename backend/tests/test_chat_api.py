"""End-to-end tests for POST /chat through FastAPI's TestClient (offline)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.agents.chat import ChatRequest, ChatService
from app.main import app
from tests.agent_helpers import EXAMPLE_QUESTION

client = TestClient(app)


@pytest.fixture(autouse=True)
def no_model_settings(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("FARMFIND_COORDINATOR", raising=False)


def _chat(message: str, conversation_id: str | None = None) -> dict:
    payload = {"message": message, "conversation_id": conversation_id, "catalog_mode": "demo_only"}
    response = client.post("/chat", json=payload)
    assert response.status_code == 200
    return response.json()


def test_full_turn_returns_reply_recommendation_and_trace():
    body = _chat(EXAMPLE_QUESTION)

    assert body["coordinator"] == "rule_based"
    assert body["follow_up_question"] is None
    assert body["reply"].startswith("Best order for Exampleville: $74.54 total")
    assert body["request"]["location"] == "Exampleville"
    recommendation = body["recommendation"]
    assert recommendation["total"] == 74.54
    assert recommendation["fulfillment"][0]["method"] == "pickup_dropoff"
    assert [item["vendor_id"] for item in recommendation["cart"]["selected_items"]] == ["vendor_g"] * 3
    assert len(recommendation["cart"]["alternative_carts"]) == 3
    assert [(step["step"], step["sender"], step["target"]) for step in body["trace"]] == [
        (1, "user", "rule_based"),
        (2, "rule_based", "catalog_agent"),
        (3, "rule_based", "distance_agent"),
        (4, "rule_based", "delivery_agent"),
        (5, "delivery_agent", "distance_agent"),
        (6, "rule_based", "cart_agent"),
    ]
    assert body["trace"][-1]["result_summary"].startswith("best cart $74.54 from Example Farm G")
    assert len({step["run_id"] for step in body["trace"]}) == 1


def test_chat_total_matches_the_optimize_endpoint_for_the_same_cart():
    chat = _chat("2 gallons of milk, I live in Exampleville")
    optimize = client.post(
        "/optimize",
        json={
            "items": [{"canonical_product": "cow_milk", "quantity": 2, "unit": "gallon"}],
            "catalog_mode": "demo_only",
        },
    ).json()

    # The sample pickup distances are measured from Exampleville, so both agree.
    assert chat["recommendation"]["total"] == optimize["total_estimated_cost"] == 27.54


def test_follow_up_turn_keeps_the_conversation_state():
    first = _chat("I need 2 gallons of milk and 3 dozen eggs")

    assert first["recommendation"] is None
    assert "Which town" in first["follow_up_question"]
    assert [step["action"] for step in first["trace"]] == ["parse_request"]

    second = _chat("I live in Samplebury", first["conversation_id"])

    assert second["conversation_id"] == first["conversation_id"]
    assert second["follow_up_question"] is None
    assert [item["product"] for item in second["request"]["items"]] == ["cow_milk", "eggs"]
    assert second["recommendation"]["feasible"] is True
    assert second["trace"][0]["run_id"] != first["trace"][0]["run_id"]


def test_unknown_conversation_id_starts_a_new_conversation():
    body = _chat("Exampleville", "does-not-exist")

    assert body["conversation_id"] != "does-not-exist"
    assert "What would you like to order?" in body["follow_up_question"]


def test_empty_message_is_rejected():
    assert client.post("/chat", json={"message": ""}).status_code == 422


# The two ways to reach the model: the setting that turns each on, and its factory.
MODEL_PATHS = pytest.mark.parametrize(
    "setting, value, factory",
    [
        ("ANTHROPIC_API_KEY", "test-key", "create_llm_coordinator"),
        ("FARMFIND_COORDINATOR", "claude_code", "create_claude_code_coordinator"),
    ],
)


@MODEL_PATHS
def test_model_path_without_its_package_uses_the_rule_based_coordinator(monkeypatch, setting, value, factory):
    def missing_package(fallback):
        raise ImportError(factory)

    monkeypatch.setenv(setting, value)
    monkeypatch.setattr(f"app.agents.chat.{factory}", missing_package)

    response = ChatService().handle(ChatRequest(message=EXAMPLE_QUESTION, catalog_mode="demo_only"))

    assert response.coordinator == "rule_based"


@MODEL_PATHS
def test_model_path_setting_selects_the_llm_coordinator(monkeypatch, setting, value, factory):
    class Recorder:
        name = "llm"

        def handle(self, message, state, bus):
            return self.fallback.handle(message, state, bus).model_copy(update={"coordinator": "llm"})

    def create(fallback):
        recorder = Recorder()
        recorder.fallback = fallback
        return recorder

    monkeypatch.setenv(setting, value)
    monkeypatch.setattr(f"app.agents.chat.{factory}", create)

    response = ChatService().handle(ChatRequest(message=EXAMPLE_QUESTION, catalog_mode="demo_only"))

    assert response.coordinator == "llm"
    assert response.recommendation.total == 74.54
