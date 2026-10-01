"""Chat service behind `POST /chat`: conversation state, agent wiring, coordinator choice."""
from __future__ import annotations

import logging
import os
from uuid import uuid4

from pydantic import BaseModel, Field

from ..schemas import CatalogMode
from .audit_log import AuditEntry
from .bus import MessageBus
from .cart_agent import CartAgent
from .catalog_agent import Catalog, CatalogAgent, load_active_catalog
from .claude_code_coordinator import create_claude_code_coordinator
from .coordinator import ConversationState, Coordinator, RuleBasedCoordinator
from .delivery_agent import DeliveryAgent
from .distance_agent import DistanceAgent
from .geo import GazetteerGeocoder, Geocoder, VendorSite, load_vendor_sites
from .llm_coordinator import create_llm_coordinator
from .messages import CartResult, ShoppingRequest

logger = logging.getLogger("farmfind.chat")

MAX_CONVERSATIONS = 200


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = None
    catalog_mode: CatalogMode | None = Field(
        default=None,
        description="Leave empty to use approved imports, or the demo samples when there are none.",
    )


class ChatResponse(BaseModel):
    conversation_id: str
    reply: str
    follow_up_question: str | None = None
    recommendation: CartResult | None = None
    request: ShoppingRequest
    trace: list[AuditEntry]
    coordinator: str


def build_bus(catalog: Catalog, geocoder: Geocoder, sites: dict[str, VendorSite]) -> MessageBus:
    bus = MessageBus()
    bus.register(CatalogAgent(catalog))
    bus.register(DistanceAgent(geocoder, sites))
    bus.register(DeliveryAgent(catalog, sites))
    bus.register(CartAgent(catalog))
    return bus


class ChatService:
    """Keeps conversations in memory and runs one coordinator turn per message."""

    def __init__(self, coordinator: Coordinator | None = None, geocoder: Geocoder | None = None):
        self.geocoder = geocoder or GazetteerGeocoder.from_file()
        self._coordinator = coordinator
        self._conversations: dict[str, ConversationState] = {}

    def handle(self, request: ChatRequest) -> ChatResponse:
        state = self._conversation(request.conversation_id)
        bus = build_bus(load_active_catalog(request.catalog_mode), self.geocoder, load_vendor_sites())
        turn = (self._coordinator or self._default_coordinator()).handle(request.message, state, bus)
        state.history.extend(
            [
                {"role": "user", "content": request.message},
                {"role": "assistant", "content": turn.reply},
            ]
        )
        return ChatResponse(
            conversation_id=state.conversation_id, trace=bus.log.entries, **turn.model_dump()
        )

    def _conversation(self, conversation_id: str | None) -> ConversationState:
        state = self._conversations.get(conversation_id or "")
        if state is None:
            if len(self._conversations) >= MAX_CONVERSATIONS:
                self._conversations.pop(next(iter(self._conversations)))
            state = ConversationState(conversation_id=uuid4().hex)
            self._conversations[state.conversation_id] = state
        return state

    def _default_coordinator(self) -> Coordinator:
        """The Claude coordinator when FARMFIND_COORDINATOR=claude_code (Claude Code
        sign-in) or an API key is set, else the rule-based one."""
        rule_based = RuleBasedCoordinator(self.geocoder)
        if os.environ.get("FARMFIND_COORDINATOR", "").strip().lower() == "claude_code":
            create, package = create_claude_code_coordinator, "claude-agent-sdk"
        elif os.environ.get("ANTHROPIC_API_KEY"):
            create, package = create_llm_coordinator, "anthropic"
        else:
            return rule_based
        try:
            return create(fallback=rule_based)
        except ImportError:
            logger.warning(
                "The %s package is not installed; using the rule-based coordinator.", package
            )
            return rule_based
