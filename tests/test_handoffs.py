import sys
from pathlib import Path

import pytest

from agent_loop.cli import main
from agent_loop.handoffs import validate_handoff


REQUEST = """# Fix Request

## Handoff Metadata

- Handoff-ID: 2026-06-15-04
- Type: request

## Requirements

### FIX-01: Repair the adapter

The adapter must run successfully.

### CHECK-01: Run the complete test suite

Record the command and result.
"""


def write_pair(tmp_path: Path, response: str) -> tuple[Path, Path]:
    handoff_dir = tmp_path / "docs" / "handoffs" / "2026-06-15"
    handoff_dir.mkdir(parents=True)
    request_path = handoff_dir / "04-fix-request.md"
    response_path = handoff_dir / "05-executor-response.md"
    request_path.write_text(REQUEST)
    response_path.write_text(response)
    return request_path, response_path


def test_validates_complete_response_with_evidence(tmp_path):
    request_path, response_path = write_pair(
        tmp_path,
        """# Executor Response

## Handoff Metadata

- Type: response
- Responds-To: 04-fix-request.md
- Overall-Status: complete

## Requirement Compliance

| Requirement | Status | Evidence | Reason |
| --- | --- | --- | --- |
| FIX-01 | complete | Commit abc123; adapter smoke test passed | - |
| CHECK-01 | complete | `.venv/bin/python -m pytest -q`: 48 passed | - |
""",
    )

    result = validate_handoff(request_path, response_path)

    assert result.valid is True
    assert result.errors == []
    assert result.requirement_statuses == {
        "FIX-01": "complete",
        "CHECK-01": "complete",
    }


def test_rejects_complete_claim_when_requirement_is_shelved(tmp_path):
    request_path, response_path = write_pair(
        tmp_path,
        """# Executor Response

## Handoff Metadata

- Type: response
- Responds-To: 04-fix-request.md
- Overall-Status: complete

## Requirement Compliance

| Requirement | Status | Evidence | Reason |
| --- | --- | --- | --- |
| FIX-01 | shelved | Investigation notes in logs/fix-01.md | Requires an upstream CLI release |
| CHECK-01 | complete | `.venv/bin/python -m pytest -q`: 48 passed | - |
""",
    )

    result = validate_handoff(request_path, response_path)

    assert result.valid is False
    assert any("Overall-Status is complete" in error for error in result.errors)


def test_accepts_shelved_requirement_with_reason_in_partial_response(tmp_path):
    request_path, response_path = write_pair(
        tmp_path,
        """# Executor Response

## Handoff Metadata

- Type: response
- Responds-To: 04-fix-request.md
- Overall-Status: partial

## Requirement Compliance

| Requirement | Status | Evidence | Reason |
| --- | --- | --- | --- |
| FIX-01 | shelved | Investigation notes in logs/fix-01.md | Requires an upstream CLI release |
| CHECK-01 | complete | `.venv/bin/python -m pytest -q`: 48 passed | - |
""",
    )

    result = validate_handoff(request_path, response_path)

    assert result.valid is True
    assert result.requirement_statuses["FIX-01"] == "shelved"


def test_rejects_missing_requirement_and_missing_shelving_reason(tmp_path):
    request_path, response_path = write_pair(
        tmp_path,
        """# Executor Response

## Handoff Metadata

- Type: response
- Responds-To: 04-fix-request.md
- Overall-Status: partial

## Requirement Compliance

| Requirement | Status | Evidence | Reason |
| --- | --- | --- | --- |
| FIX-01 | shelved | Investigation attempted | - |
""",
    )

    result = validate_handoff(request_path, response_path)

    assert result.valid is False
    assert any("CHECK-01" in error and "missing" in error for error in result.errors)
    assert any("FIX-01" in error and "reason" in error for error in result.errors)


def test_rejects_complete_requirement_without_evidence(tmp_path):
    request_path, response_path = write_pair(
        tmp_path,
        """# Executor Response

## Handoff Metadata

- Type: response
- Responds-To: 04-fix-request.md
- Overall-Status: complete

## Requirement Compliance

| Requirement | Status | Evidence | Reason |
| --- | --- | --- | --- |
| FIX-01 | complete | - | - |
| CHECK-01 | complete | Tests passed | - |
""",
    )

    result = validate_handoff(request_path, response_path)

    assert result.valid is False
    assert any("FIX-01" in error and "evidence" in error for error in result.errors)


def test_rejects_response_outside_dated_sequence(tmp_path):
    request_path, response_path = write_pair(
        tmp_path,
        """# Executor Response

## Handoff Metadata

- Type: response
- Responds-To: 04-fix-request.md
- Overall-Status: complete

## Requirement Compliance

| Requirement | Status | Evidence | Reason |
| --- | --- | --- | --- |
| FIX-01 | complete | Commit abc123 | - |
| CHECK-01 | complete | 48 passed | - |
""",
    )
    misplaced_response = tmp_path / "05-executor-response.md"
    response_path.rename(misplaced_response)

    result = validate_handoff(request_path, misplaced_response)

    assert result.valid is False
    assert any("same dated handoff directory" in error for error in result.errors)


def test_rejects_non_adjacent_response_and_mismatched_handoff_id(tmp_path):
    request_path, response_path = write_pair(
        tmp_path,
        """# Executor Response

## Handoff Metadata

- Type: response
- Responds-To: 04-fix-request.md
- Overall-Status: complete

## Requirement Compliance

| Requirement | Status | Evidence | Reason |
| --- | --- | --- | --- |
| FIX-01 | complete | Commit abc123 | - |
| CHECK-01 | complete | 48 passed | - |
""",
    )
    request_path.write_text(REQUEST.replace("2026-06-15-04", "2026-06-14-99"))
    skipped_response = response_path.with_name("06-executor-response.md")
    response_path.rename(skipped_response)

    result = validate_handoff(request_path, skipped_response)

    assert result.valid is False
    assert any("Handoff-ID" in error and "2026-06-15-04" in error for error in result.errors)
    assert any("immediately follow" in error for error in result.errors)


def test_cli_validates_handoff_and_reports_requirement_count(tmp_path, capsys, monkeypatch):
    request_path, response_path = write_pair(
        tmp_path,
        """# Executor Response

## Handoff Metadata

- Type: response
- Responds-To: 04-fix-request.md
- Overall-Status: complete

## Requirement Compliance

| Requirement | Status | Evidence | Reason |
| --- | --- | --- | --- |
| FIX-01 | complete | Commit abc123 | - |
| CHECK-01 | complete | 48 passed | - |
""",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["agent-loop", "handoff", "validate", str(request_path), str(response_path)],
    )

    main()

    captured = capsys.readouterr()
    assert "Handoff validation passed" in captured.out
    assert "2 requirements" in captured.out
    assert "Trusted-host execution" not in captured.out


def test_cli_returns_nonzero_and_prints_validation_errors(tmp_path, capsys, monkeypatch):
    request_path, response_path = write_pair(
        tmp_path,
        """# Executor Response

## Handoff Metadata

- Type: response
- Responds-To: 04-fix-request.md
- Overall-Status: complete

## Requirement Compliance

| Requirement | Status | Evidence | Reason |
| --- | --- | --- | --- |
| FIX-01 | shelved | Notes | Too risky for this pass |
| CHECK-01 | complete | 48 passed | - |
""",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["agent-loop", "handoff", "validate", str(request_path), str(response_path)],
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "Handoff validation failed" in captured.err
    assert "Overall-Status is complete" in captured.err
