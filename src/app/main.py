"""ASGI application and preserved V0 command-line interface."""

import argparse
import sys
from collections.abc import Sequence

from app.api.application import create_app
from app.config import ConfigurationError, load_settings
from app.llm.client import GeminiStructuredClient, LLMClientError
from app.llm.models import QuestionAnalysis

app = create_app()


def build_parser() -> argparse.ArgumentParser:
    """Build the deliberately small V0 CLI parser."""

    parser = argparse.ArgumentParser(
        prog="enterprise-ai",
        description="Classify one employee question with a schema-validated Gemini response.",
    )
    parser.add_argument("question", nargs="+", help="the employee question to analyse")
    return parser


def format_result(result: QuestionAnalysis) -> str:
    """Render a validated response for a terminal user."""

    action = "Yes" if result.requires_action else "No"
    return "\n".join(
        (
            f"Category: {result.category.value}",
            f"Summary: {result.summary}",
            f"Requires action: {action}",
            f"Confidence: {result.confidence:.0%}",
        )
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run one CLI request and convert known failures to safe terminal output."""

    args = build_parser().parse_args(argv)
    question = " ".join(args.question)

    try:
        settings = load_settings()
        result = GeminiStructuredClient(settings).analyze(question)
    except ConfigurationError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except LLMClientError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 3
    except Exception:
        print(
            "Error: The application could not complete the request. Please try again.",
            file=sys.stderr,
        )
        return 1

    print(format_result(result))
    return 0


def run() -> None:
    """Console-script entry point."""

    raise SystemExit(main())


if __name__ == "__main__":
    run()
