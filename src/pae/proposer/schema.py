"""The constrained HA-automation subset the LLM may author, and its validation.

Declarative only: time/sun/state triggers, time/state/sun conditions, service
actions. No delay/wait/templates/scripts — pydantic extra="forbid" plus the
checks below. A validation failure never stores anything."""
from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
)

from pae.miner.stats import circular_diff

ALLOWED_SERVICES: dict[str, set[str]] = {
    "light": {"turn_on", "turn_off"},
    "switch": {"turn_on", "turn_off"},
    "fan": {"turn_on", "turn_off"},
    "media_player": {"turn_on", "turn_off"},
    "lock": {"lock", "unlock"},
    "cover": {"open_cover", "close_cover"},
}
SERVICE_STATE: dict[str, str] = {
    "turn_on": "on",
    "turn_off": "off",
    "lock": "locked",
    "unlock": "unlocked",
    "open_cover": "open",
    "close_cover": "closed",
}
WEEKDAYS = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _parse_hms(value: str) -> float:
    """'HH:MM:SS' -> minutes-of-day; raises ValueError on junk."""
    h, m, s = value.split(":")
    if not (0 <= int(h) <= 23 and 0 <= int(m) <= 59 and 0 <= int(s) <= 59):
        raise ValueError(f"not a time of day: {value!r}")
    return int(h) * 60 + int(m) + int(s) / 60


def _parse_offset(value: str) -> float:
    """'[+-]HH:MM:SS' -> signed minutes; raises ValueError on junk."""
    sign = -1.0 if value.startswith("-") else 1.0
    return sign * _parse_hms(value.lstrip("+-"))


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TimeTrigger(_Strict):
    platform: Literal["time"]
    at: str

    @field_validator("at")
    @classmethod
    def _at_is_time(cls, v: str) -> str:
        _parse_hms(v)
        return v


class SunTrigger(_Strict):
    platform: Literal["sun"]
    event: Literal["sunrise", "sunset"]
    offset: str = "00:00:00"

    @field_validator("offset")
    @classmethod
    def _offset_ok(cls, v: str) -> str:
        _parse_offset(v)
        return v


class StateTrigger(_Strict):
    platform: Literal["state"]
    entity_id: str
    to: str
    from_: str | None = Field(default=None, alias="from")


Trigger = Annotated[
    TimeTrigger | SunTrigger | StateTrigger, Field(discriminator="platform")
]


class TimeCondition(_Strict):
    condition: Literal["time"]
    weekday: list[WEEKDAYS] | None = None
    after: str | None = None
    before: str | None = None

    @field_validator("after", "before")
    @classmethod
    def _after_before_are_times(cls, v: str | None) -> str | None:
        if v is not None:
            _parse_hms(v)
        return v


class StateCondition(_Strict):
    condition: Literal["state"]
    entity_id: str
    state: str


class SunCondition(_Strict):
    condition: Literal["sun"]
    after: Literal["sunrise", "sunset"] | None = None
    before: Literal["sunrise", "sunset"] | None = None


Condition = Annotated[
    TimeCondition | StateCondition | SunCondition, Field(discriminator="condition")
]


def _listify(v):
    """LLMs emit idiomatic HA shapes (scalar entity_id, single trigger object);
    accept and normalize instead of failing the whole proposal on shape alone."""
    return [v] if not isinstance(v, list) else v


_Listified = BeforeValidator(_listify)


class Target(_Strict):
    entity_id: Annotated[list[str], _Listified] = Field(min_length=1)


class ServiceAction(_Strict):
    service: str
    target: Target


class Automation(_Strict):
    trigger: Annotated[list[Trigger], _Listified] = Field(min_length=1, max_length=1)
    condition: Annotated[list[Condition], _Listified] = Field(default_factory=list)
    action: Annotated[list[ServiceAction], _Listified] = Field(min_length=1)


def validate_proposal(
    automation: dict,
    group,
    *,
    registry_domains: dict[str, str],
    tz,
    sun,
    grace_minutes: float = 60.0,
) -> tuple[Automation | None, list[str]]:
    try:
        parsed = Automation.model_validate(automation)
    except ValidationError as exc:
        return None, [str(e["loc"]) + ": " + e["msg"] for e in exc.errors()]

    errors: list[str] = []
    allowed_entities = set(group.entity_ids)
    if group.trigger_entity_id:
        allowed_entities.add(group.trigger_entity_id)

    referenced: list[str] = []
    for t in parsed.trigger:
        if isinstance(t, StateTrigger):
            referenced.append(t.entity_id)
    for c in parsed.condition:
        if isinstance(c, StateCondition):
            referenced.append(c.entity_id)
    for a in parsed.action:
        referenced.extend(a.target.entity_id)
    for e in referenced:
        if e not in allowed_entities:
            errors.append(f"entity {e} is not part of the mined pattern group")
        elif e not in registry_domains:
            errors.append(f"entity {e} is not in the registry")

    for a in parsed.action:
        if "." not in a.service:
            errors.append(f"malformed service {a.service!r}")
            continue
        s_domain, s_name = a.service.split(".", 1)
        if s_name not in ALLOWED_SERVICES.get(s_domain, set()):
            errors.append(f"service {a.service} not in the allowed set")
        for e in a.target.entity_id:
            if registry_domains.get(e) not in (None, s_domain):
                errors.append(f"service {a.service} does not match domain of {e}")

    trig = parsed.trigger[0]
    if group.kind == "time_of_day":
        if isinstance(trig, TimeTrigger):
            minutes = _parse_hms(trig.at)
        elif isinstance(trig, SunTrigger):
            if sun is None:
                errors.append("sun trigger but sun calculation is disabled (no coordinates)")
                minutes = None
            else:
                today = datetime.now(UTC).astimezone(tz).date()
                sm = sun.sun_minutes(today)
                minutes = (
                    None
                    if sm is None
                    else sm[0 if trig.event == "sunrise" else 1] + _parse_offset(trig.offset)
                )
        else:
            errors.append("time_of_day proposals must use a time or sun trigger")
            minutes = None
        if minutes is not None and abs(circular_diff(minutes, group.mean_minutes)) > grace_minutes:
            errors.append(
                f"trigger time {minutes:.0f}min is over {grace_minutes:.0f}min from the "
                f"mined mean {group.mean_minutes:.0f}min"
            )
    else:  # event_pair
        if not isinstance(trig, StateTrigger):
            errors.append("event_pair proposals must use a state trigger")
        elif trig.entity_id != group.trigger_entity_id or trig.to != group.trigger_state:
            errors.append("state trigger must match the mined trigger entity and state")

    return (parsed, []) if not errors else (None, errors)
