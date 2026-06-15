import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List


DATE_DIR_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
HANDOFF_FILE_PATTERN = re.compile(
    r"^(?P<sequence>\d{2})-.+-(?P<kind>request|response|review)\.md$"
)
REQUIREMENT_PATTERN = re.compile(
    r"^###\s+(?P<requirement>[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+)\b",
    re.MULTILINE,
)
METADATA_PATTERN = re.compile(
    r"^-\s+(?P<key>[A-Za-z][A-Za-z-]*):\s*(?P<value>.+?)\s*$",
    re.MULTILINE,
)
ALLOWED_REQUIREMENT_STATUSES = {"complete", "blocked", "shelved"}
ALLOWED_OVERALL_STATUSES = {"complete", "partial", "blocked"}
EMPTY_VALUES = {"", "-", "n/a", "none", "not applicable"}


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    errors: List[str]
    requirement_statuses: Dict[str, str]


def _read(path: Path, errors: List[str], label: str) -> str:
    try:
        return path.read_text()
    except OSError as exc:
        errors.append(f"Could not read {label} file {path}: {exc}")
        return ""


def _metadata(markdown: str) -> Dict[str, str]:
    return {
        match.group("key").lower(): match.group("value").strip()
        for match in METADATA_PATTERN.finditer(markdown)
    }


def _table_rows(markdown: str) -> List[Dict[str, str]]:
    lines = markdown.splitlines()
    for index, line in enumerate(lines):
        if not line.strip().startswith("|"):
            continue
        headers = [cell.strip().lower() for cell in line.strip().strip("|").split("|")]
        if headers != ["requirement", "status", "evidence", "reason"]:
            continue
        rows = []
        for row_line in lines[index + 2 :]:
            if not row_line.strip().startswith("|"):
                break
            cells = [cell.strip() for cell in row_line.strip().strip("|").split("|")]
            if len(cells) != len(headers):
                continue
            rows.append(dict(zip(headers, cells)))
        return rows
    return []


def _is_empty(value: str) -> bool:
    return value.strip().lower() in EMPTY_VALUES


def validate_handoff(request_path: Path, response_path: Path) -> ValidationResult:
    request_path = Path(request_path)
    response_path = Path(response_path)
    errors: List[str] = []

    if request_path.parent != response_path.parent:
        errors.append("Request and response must be in the same dated handoff directory.")
    if not DATE_DIR_PATTERN.fullmatch(request_path.parent.name):
        errors.append("Handoff files must be stored under a YYYY-MM-DD directory.")

    request_match = HANDOFF_FILE_PATTERN.fullmatch(request_path.name)
    response_match = HANDOFF_FILE_PATTERN.fullmatch(response_path.name)
    if not request_match or request_match.group("kind") != "request":
        errors.append("Request filename must use NN-description-request.md.")
    if not response_match or response_match.group("kind") != "response":
        errors.append("Response filename must use NN-description-response.md.")
    if request_match and response_match:
        expected_response_sequence = int(request_match.group("sequence")) + 1
        if int(response_match.group("sequence")) != expected_response_sequence:
            errors.append("Response sequence number must immediately follow the request sequence number.")

    request_markdown = _read(request_path, errors, "request")
    response_markdown = _read(response_path, errors, "response")
    request_metadata = _metadata(request_markdown)
    response_metadata = _metadata(response_markdown)

    if request_metadata.get("type", "").lower() != "request":
        errors.append("Request metadata must contain '- Type: request'.")
    if not request_metadata.get("handoff-id"):
        errors.append("Request metadata must contain a Handoff-ID.")
    elif request_match:
        expected_handoff_id = (
            f"{request_path.parent.name}-{request_match.group('sequence')}"
        )
        if request_metadata["handoff-id"] != expected_handoff_id:
            errors.append(
                f"Request Handoff-ID must be {expected_handoff_id} for its directory and sequence."
            )
    if response_metadata.get("type", "").lower() != "response":
        errors.append("Response metadata must contain '- Type: response'.")
    if response_metadata.get("responds-to") != request_path.name:
        errors.append(f"Response metadata must contain '- Responds-To: {request_path.name}'.")

    overall_status = response_metadata.get("overall-status", "").lower()
    if overall_status not in ALLOWED_OVERALL_STATUSES:
        errors.append(
            "Overall-Status must be one of: blocked, complete, partial."
        )

    requirements = REQUIREMENT_PATTERN.findall(request_markdown)
    duplicate_requirements = sorted(
        requirement for requirement in set(requirements) if requirements.count(requirement) > 1
    )
    for requirement in duplicate_requirements:
        errors.append(f"Request requirement {requirement} is declared more than once.")
    if not requirements:
        errors.append("Request must declare at least one requirement heading such as '### FIX-01: ...'.")

    rows = _table_rows(response_markdown)
    if not rows:
        errors.append(
            "Response must contain a Requirement Compliance table with Requirement, Status, Evidence, and Reason columns."
        )

    row_map: Dict[str, Dict[str, str]] = {}
    for row in rows:
        requirement = row["requirement"].strip()
        if requirement in row_map:
            errors.append(f"Response requirement {requirement} is listed more than once.")
            continue
        row_map[requirement] = row

    expected = set(requirements)
    actual = set(row_map)
    for requirement in sorted(expected - actual):
        errors.append(f"Requirement {requirement} is missing from the response compliance table.")
    for requirement in sorted(actual - expected):
        errors.append(f"Response contains unknown requirement {requirement}.")

    requirement_statuses: Dict[str, str] = {}
    for requirement in requirements:
        row = row_map.get(requirement)
        if not row:
            continue
        status = row["status"].strip().lower()
        requirement_statuses[requirement] = status
        if status not in ALLOWED_REQUIREMENT_STATUSES:
            errors.append(
                f"Requirement {requirement} has invalid status '{status}'; use complete, blocked, or shelved."
            )
            continue
        if status == "complete" and _is_empty(row["evidence"]):
            errors.append(f"Completed requirement {requirement} must include evidence.")
        if status in {"blocked", "shelved"} and _is_empty(row["reason"]):
            errors.append(f"{status.title()} requirement {requirement} must include a reason.")

    if overall_status == "complete":
        incomplete = [
            requirement
            for requirement in requirements
            if requirement_statuses.get(requirement) != "complete"
        ]
        if incomplete:
            errors.append(
                "Overall-Status is complete but these requirements are not complete: "
                + ", ".join(incomplete)
                + "."
            )

    return ValidationResult(
        valid=not errors,
        errors=errors,
        requirement_statuses=requirement_statuses,
    )
