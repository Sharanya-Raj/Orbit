"""
Orbit session logger.
Writes timestamped logs to logs/orbit_YYYY-MM-DD_HH-MM-SS.log
One file per process run.
"""
import json
import logging
from datetime import datetime
from pathlib import Path

# ------------------------------------------------------------------
# Setup — runs once on first import
# ------------------------------------------------------------------
_LOGS_DIR = Path(__file__).parent.parent / "logs"
_LOGS_DIR.mkdir(exist_ok=True)

_session_start = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
LOG_FILE = _LOGS_DIR / f"orbit_{_session_start}.log"

_logger = logging.getLogger("orbit")
_logger.setLevel(logging.DEBUG)

_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
_handler.setLevel(logging.DEBUG)
_handler.setFormatter(
    logging.Formatter(
        fmt="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
)
_logger.addHandler(_handler)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------
def _format_messages(messages: list) -> str:
    """Pretty-prints a list of chat messages, replacing base64 blobs with [IMAGE]."""
    lines = []
    for m in messages:
        role = m.get("role", "?").upper()
        content = m.get("content", "")
        if isinstance(content, list):
            parts = []
            for part in content:
                if part.get("type") == "text":
                    parts.append(part["text"])
                elif part.get("type") == "image_url":
                    parts.append("[IMAGE]")
                else:
                    parts.append(str(part))
            content = "\n      ".join(parts)
        lines.append(f"  [{role}] {content}")
    return "\n".join(lines)


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------
def log(msg: str) -> None:
    """General info log."""
    _logger.info(msg)


def log_error(msg: str) -> None:
    _logger.error(msg)


def log_llm_prompt(model: str, messages: list) -> None:
    """Log everything sent to an LLM (images shown as [IMAGE])."""
    _logger.info(
        f"[PROMPT → {model}]\n{_format_messages(messages)}"
    )


def log_llm_response(model: str, content: str) -> None:
    """Log the raw text returned by an LLM."""
    _logger.info(
        f"[RESPONSE ← {model}]\n  {content}"
    )


def log_action(action: dict) -> None:
    """Log the action the agent is about to execute."""
    _logger.info(
        f"[ACTION] {json.dumps(action)}"
    )


def log_session_start(instruction: str) -> None:
    _logger.info("=" * 70)
    _logger.info(f"SESSION START  |  instruction: {instruction!r}")
    _logger.info("=" * 70)


def log_session_end(result: str) -> None:
    _logger.info(f"SESSION END  |  result: {result!r}")
    _logger.info("=" * 70)


def log_screenshot(step: int, path: str) -> None:
    """Log the file path of the screenshot taken at this step."""
    _logger.info(f"[SCREENSHOT] step={step} path={path!r}")


def log_validation(action_type: str, success: bool, error: str = "") -> None:
    """Log the result of Pydantic action validation."""
    if success:
        _logger.info(f"[VALIDATION OK] type={action_type!r}")
    else:
        _logger.warning(f"[VALIDATION FAIL] type={action_type!r} error={error!r}")


def log_execution_result(action_type: str, success: bool, error: str = "") -> None:
    """Log whether an action executed successfully."""
    if success:
        _logger.info(f"[EXEC OK] type={action_type!r}")
    else:
        _logger.error(f"[EXEC FAIL] type={action_type!r} error={error!r}")


def log_screen_diff(step: int, action_type: str, changed: bool, distance: int) -> None:
    """Log the perceptual hash diff result after an action."""
    _logger.info(
        f"[SCREEN DIFF] step={step} action={action_type!r} "
        f"changed={changed} hamming={distance}"
    )
