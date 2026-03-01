"""
Pydantic v2 action models for the Orbit agent.
Every LLM-generated action is validated against these schemas before execution.
"""
from __future__ import annotations
from typing import Annotated, Literal, Union
from pydantic import BaseModel, Field, field_validator, TypeAdapter, ValidationError


# ---------------------------------------------------------------------------
# Shared types
# ---------------------------------------------------------------------------

BBoxCoord = Annotated[float, Field(ge=0.0, le=1000.0)]
ContextType = Literal["os", "browser"]


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class BaseAction(BaseModel):
    thought: str = ""
    context: ContextType = "os"
    # Extra fields from the LLM (e.g., stray keys) are silently ignored
    model_config = {"extra": "allow"}


# ---------------------------------------------------------------------------
# Action models — one per action type
# ---------------------------------------------------------------------------

class ClickBoxAction(BaseAction):
    type: Literal["click_box"]
    bbox: list[BBoxCoord] = Field(..., min_length=4, max_length=4)
    context: ContextType = "os"

    @field_validator("bbox")
    @classmethod
    def bbox_must_be_ordered(cls, v: list[float]) -> list[float]:
        ymin, xmin, ymax, xmax = v
        if ymax <= ymin:
            raise ValueError(f"ymax ({ymax}) must be greater than ymin ({ymin})")
        if xmax <= xmin:
            raise ValueError(f"xmax ({xmax}) must be greater than xmin ({xmin})")
        return v


class TypeTextAction(BaseAction):
    type: Literal["type_text"]
    text: str = Field(..., min_length=1)


class PressKeyAction(BaseAction):
    type: Literal["press_key"]
    key: str = Field(..., min_length=1)


class PressShortcutAction(BaseAction):
    type: Literal["press_shortcut"]
    keys: list[str] = Field(..., min_length=1)


class OpenAppAction(BaseAction):
    type: Literal["open_app"]
    app: str = Field(..., min_length=1)
    context: ContextType = "os"


class MaximizeWindowAction(BaseAction):
    type: Literal["maximize_window"]
    context: ContextType = "os"


class ClickElementAction(BaseAction):
    type: Literal["click_element"]
    selector: str = Field(..., min_length=1)
    context: ContextType = "browser"


class SpeakAction(BaseAction):
    type: Literal["speak"]
    text: str = Field(..., min_length=1)


class WaitAction(BaseAction):
    type: Literal["wait"]
    ms: int = Field(default=1000, ge=0, le=30000)


class DoneAction(BaseAction):
    type: Literal["done"]
    message: str = "Task complete."


class RequestUserInputAction(BaseAction):
    type: Literal["request_user_input"]
    prompt: str = Field(..., min_length=1)


# ---------------------------------------------------------------------------
# Discriminated union — routes to the correct model via the "type" key
# ---------------------------------------------------------------------------

AnyAction = Annotated[
    Union[
        ClickBoxAction,
        TypeTextAction,
        PressKeyAction,
        PressShortcutAction,
        OpenAppAction,
        MaximizeWindowAction,
        ClickElementAction,
        SpeakAction,
        WaitAction,
        DoneAction,
        RequestUserInputAction,
    ],
    Field(discriminator="type"),
]

# Module-level adapter — created once, reused on every call
_adapter: TypeAdapter = TypeAdapter(AnyAction)


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def validate_action(raw: dict) -> tuple:
    """
    Validate a single raw action dict.
    Returns (model, "") on success, or (None, error_string) on failure.
    The error string is safe to inject back into the LLM conversation.
    """
    try:
        model = _adapter.validate_python(raw)
        return model, ""
    except ValidationError as exc:
        errors = "; ".join(
            f"field '{'.'.join(str(loc) for loc in e['loc'])}': {e['msg']}"
            for e in exc.errors()
        )
        return None, f"Action validation failed: {errors}"


def validate_action_list(raws: list) -> tuple:
    """
    Validate a list of raw action dicts (a batch from the LLM).
    Returns (list[model], "") on success, or ([], first_error_string) on failure.
    Stops at the first invalid action.
    """
    validated = []
    for i, raw in enumerate(raws):
        model, err = validate_action(raw)
        if err:
            return [], f"Action[{i}] {err}"
        validated.append(model)
    return validated, ""
