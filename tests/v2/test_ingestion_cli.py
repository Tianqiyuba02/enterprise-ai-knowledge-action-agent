from app.config import ConfigurationError
from app.ingestion.cli import _format_result, build_parser, main
from app.ingestion.models import IngestionOutcome, IngestionResult


def test_cli_supports_one_file_and_default_corpus_directory() -> None:
    parser = build_parser()

    file_args = parser.parse_args(["file", "policy.md"])
    corpus_args = parser.parse_args(["corpus"])

    assert file_args.command == "file"
    assert str(file_args.path) == "policy.md"
    assert corpus_args.command == "corpus"
    assert str(corpus_args.path) == "corpus/v2"


def test_cli_formats_only_safe_ingestion_metadata() -> None:
    result = IngestionResult(
        outcome=IngestionOutcome.INSERTED,
        doc_code="POL-HR-001",
        version="2.0",
        chunk_count=4,
    )

    output = _format_result(result)

    assert output == ("inserted: POL-HR-001 v2.0; chunks=4; embedding=gemini-embedding-2/768")
    assert "[" not in output


def test_cli_returns_nonzero_with_safe_configuration_error(monkeypatch, capsys) -> None:
    def fail_settings():
        raise ConfigurationError

    monkeypatch.setattr("app.ingestion.cli.load_settings", fail_settings)

    exit_code = main(["file", "policy.md"])

    assert exit_code == 2
    assert "GEMINI_API_KEY" in capsys.readouterr().err
