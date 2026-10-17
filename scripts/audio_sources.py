"""Explicit non-character speech; never infer a machine from its display name."""

NON_CHARACTER = "non_character"
ANNOUNCEMENT = "非人物播报"


def validate_source(event, record):
    """Validate the source type before character lookup; preserve legacy events."""
    non_character = record.get("source_kind") == NON_CHARACTER
    if non_character:
        label = record.get("speaker")
        if (event.get("source_kind") != NON_CHARACTER or event.get("voice") != ANNOUNCEMENT
                or record.get("voice") != ANNOUNCEMENT):
            raise ValueError("非人物播报须与已复核来源类型一致")
        if (not isinstance(label, str) or not label.strip() or any(c in label for c in "\r\n@{}")
                or event.get("source_label") != label):
            raise ValueError("非人物播报source_label须逐字对应已复核来源声源")
        if "character_id" in event or "character_ids" in event:
            raise ValueError("非人物播报不得关联人物资产或借在场人物发声")
    elif event.get("source_kind", "character") != "character" or "source_label" in event:
        raise ValueError("人物对白不得改为非人物播报或替换来源声源")
    return non_character


def render_source(event, reference):
    """A broadcast label is plain source text, never a Canvas asset reference."""
    if event.get("source_kind") == NON_CHARACTER:
        return event["source_label"]
    return reference(event["character_id"])
