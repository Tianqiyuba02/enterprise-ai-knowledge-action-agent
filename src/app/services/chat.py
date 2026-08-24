"""Application service exposing the existing validated V0 LLM capability."""

from app.llm.client import GeminiStructuredClient
from app.llm.models import QuestionAnalysis


class ChatService:
    def __init__(self, llm_client: GeminiStructuredClient) -> None:
        self._llm_client = llm_client

    def analyze_question(self, question: str) -> QuestionAnalysis:
        return self._llm_client.analyze(question)
