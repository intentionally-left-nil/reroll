import pytest

from reroll import main


def test_main_smoke(capsys: pytest.CaptureFixture[str]) -> None:
    """Placeholder smoke test so the harness has at least one real, passing test."""
    main()
    captured = capsys.readouterr()
    assert "reroll" in captured.out.lower()
