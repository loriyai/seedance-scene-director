"""Shared source-person slots; visual variants never add a second source person."""


def split_cast_entry(entry: str) -> tuple[str, str]:
    if not isinstance(entry, str) or "-" not in entry:
        raise ValueError(f"人物条目缺少名称或形象：{entry}")
    if "-群像-" in entry:
        prefix, image = entry.split("-群像-", 1)
        name = prefix + "-群像"
    else:
        name, image = entry.split("-", 1)
    name, image = name.strip(), image.strip()
    if not name or not image or any(not part.strip() for part in entry.split("-")):
        raise ValueError(f"人物条目缺少名称或形象：{entry}")
    return name, image


def validate_source_speaker(item: dict, *, context: str = "") -> None:
    """Check the optional source label against the same canonical cast slot.

    This is a field contract, not proof of dialogue. Background groups normally
    omit source_speaker; an existing exact slot label remains compatible.
    """
    if "source_speaker" not in item:
        return
    label = f"{context} {item.get('id', '')}".strip()
    speaker = item["source_speaker"]
    if not isinstance(speaker, str) or not speaker.strip():
        raise ValueError(f"{label} source_speaker须为非空字符串")
    expected, _ = split_cast_entry(item.get("source_entry", item.get("name")))
    if speaker != expected:
        raise ValueError(f"{label} 人物声源须对应原人物行：source_speaker={speaker!r}，人物槽位={expected!r}")


def validate_cast_entries(entries: list[str]) -> list[str]:
    """Return all deterministic errors; do not infer aliases or merge identities."""
    errors, seen = [], set()
    if not isinstance(entries, list):
        return ["人物条目须为数组"]
    for entry in entries:
        try:
            name, _ = split_cast_entry(entry)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if name in seen:
            errors.append(f"同一场人物槽位重复：{name}；基础与佩戴/换装状态不能登记为两个人")
        seen.add(name)
        if "非人物播报" in entry:
            errors.append("非人物播报声源不得登记为人物槽位")
    return errors
