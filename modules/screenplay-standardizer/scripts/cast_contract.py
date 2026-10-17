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
