"""Prompt v1: frame one pattern group as an automation-drafting request.

The model may decline (propose=false) — it doubles as a plausibility filter.
The response is constrained by RESPONSE_SCHEMA via Ollama structured output;
everything the model returns is re-validated by pae.proposer.schema before
storage, so this prompt is about giving good evidence, not enforcing rules."""
from dataclasses import dataclass

PROMPT_VERSION = 1

RESPONSE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "propose": {"type": "boolean"},
        "decline_reason": {"type": "string"},
        "title": {"type": "string"},
        "rationale": {"type": "string"},
        "automation": {"type": "object"},
    },
    "required": ["propose"],
}

SYSTEM = """You draft Home Assistant automations from observed human habits.

You will get one mined behavior pattern group: statistics about manual actions a
household member performs regularly, plus context about each entity involved.

Decide whether this habit makes sense as an automation a careful home operator
would want. If not (too personal, safety-relevant, coincidental, or better left
manual), return {"propose": false, "decline_reason": "..."}.

If yes, return propose=true with:
- title: short, human, specific (e.g. "Morning path lights on weekdays")
- rationale: 2-3 sentences: what was observed and why automating it helps
- automation: a Home Assistant automation object using ONLY:
  - exactly one trigger: {"platform":"time","at":"HH:MM:SS"} or
    {"platform":"sun","event":"sunset"|"sunrise","offset":"[+-]HH:MM:SS"} or
    {"platform":"state","entity_id":...,"to":...}
  - optional conditions: {"condition":"time","weekday":[...]} or
    {"condition":"state",...} or {"condition":"sun",...}
  - actions: {"service":"<domain>.<service>","target":{"entity_id":[...]}} only
Use only the entities given. No templates, delays, or scripts. If the observed
times track sunset or sunrise, prefer a sun trigger over a fixed time."""


@dataclass
class RegistryInfo:
    domain: str
    friendly_name: str | None
    area_name: str | None
    device_class: str | None


def _entity_line(entity_id: str, info: RegistryInfo | None) -> str:
    if info is None:
        return f"- {entity_id}"
    parts = [f"- {entity_id}: \"{info.friendly_name or entity_id}\""]
    if info.area_name:
        parts.append(f"area: {info.area_name}")
    if info.device_class:
        parts.append(f"class: {info.device_class}")
    return ", ".join(parts)


def _hhmm(minutes: float) -> str:
    return f"{int(minutes // 60):02d}:{int(minutes % 60):02d}"


def build_messages(group, registry, tz) -> list[dict]:
    lines: list[str] = []
    entity_ids = set(group.entity_ids)
    if group.trigger_entity_id:
        entity_ids.add(group.trigger_entity_id)
    lines.append("Entities:")
    for e in sorted(entity_ids):
        lines.append(_entity_line(e, registry.get(e)))
    lines.append("")

    if group.kind == "time_of_day":
        lines.append(
            f"Habit: on {group.day_type} days, around {_hhmm(group.mean_minutes)} local "
            f"({tz.key}), these manual actions occur together:"
        )
        for p in group.patterns:
            lines.append(
                f"- {p.entity_id} -> {p.action} at ~{_hhmm(p.tod_minutes)} "
                f"(±{p.tod_std_minutes:.0f} min, {p.occurrences} times over "
                f"{p.days_observed} days, support {p.support:.2f}, "
                f"consistency {p.temporal_consistency:.2f})"
            )
            sample = (p.evidence or {}).get("sample_times", [])[-3:]
            if sample:
                lines.append(f"  recent occurrences: {', '.join(sample)}")
    else:
        lines.append(
            f"Habit: after {group.trigger_entity_id} changes to "
            f"'{group.trigger_state}', these manual actions follow within minutes:"
        )
        for p in group.patterns:
            lines.append(
                f"- {p.entity_id} -> {p.action} (confidence {p.confidence:.2f}, "
                f"lift {p.lift:.1f}, {p.occurrences} times)"
            )

    lines.append("")
    lines.append("Draft the automation, or decline.")
    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": "\n".join(lines)},
    ]
