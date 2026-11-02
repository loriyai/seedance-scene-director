"""Source-declared, scene-local clothing names; dialogue content is never mapped."""

import re

POLICY = "source-scene-clothing-names-v1"


def cast_entries(value):
    match = re.fullmatch(r"人物\s*[:：]\s*(.*?)\s*[；;]?", value.strip())
    if not match:
        return None
    return [part.strip() for part in re.split(r"[；;、，,]", match.group(1).rstrip("；;"))
            if part.strip() and part.strip() != "无"]


def scene_mapping(paragraphs, start, end):
    """Only a declaration inside this source range authorizes a clothing name."""
    declarations = [(i, cast_entries(paragraphs[i - 1])) for i in range(start, end + 1)]
    declarations = [(i, entries) for i, entries in declarations if entries is not None]
    mapping, previous = {}, None
    for _, entries in declarations:
        current = {}
        for entry in entries:
            if "-" not in entry or "-群像-" in entry:
                continue
            base, suffix = entry.split("-", 1)
            if not base.strip() or not suffix.strip():
                raise ValueError("原人物行的换装角色须有完整基础名和服装限定")
            if base in current:
                raise ValueError("原人物行同一基础角色的换装声明不唯一：" + base)
            current[base] = entry
        if previous is not None and current != previous:
            raise ValueError("同一标准场包含不同原人物行，请按原场分别核对换装名称")
        mapping = current
        previous = current
    return mapping, dict(declarations)


def replace_names(text, mapping, known=(), ambiguous=None):
    """Longest exact names first; protect full names and other known characters."""
    names = set(mapping) | set(mapping.values()) | set(known)
    if not names:
        return text
    pattern = re.compile("|".join(re.escape(name) for name in sorted(names, key=len, reverse=True)))
    def replacement(match):
        name = match.group()
        # Never replace a prefix of an already qualified name or ASCII token.
        left = text[match.start() - 1:match.start()] if match.start() else ""
        right = text[match.end():match.end() + 1]
        if (name in mapping and len(name) == 1
                and any(re.match(r"[\u3400-\u9fff]", char) for char in (left, right) if char)):
            if ambiguous is not None:
                ambiguous.append(name)
            return name
        if (left and re.match(r"[A-Za-z0-9_]", left)) or (right and re.match(r"[-A-Za-z0-9_]", right)):
            return name
        return mapping.get(name, name)
    return pattern.sub(replacement, text)


def action_names(text, mapping, known=(), ambiguous=None):
    """Protect all quoted content, including original names and punctuation."""
    pairs = {'“': '”', '‘': '’', '「': '」', '『': '』', '"': '"'}
    result, plain, quoted, stack = [], [], [], []
    for char in text:
        if not stack:
            if char in pairs:
                result.append(replace_names(''.join(plain), mapping, known, ambiguous))
                plain = []
                quoted = [char]
                stack.append(pairs[char])
            else:
                plain.append(char)
        else:
            quoted.append(char)
            if char == stack[-1]:
                stack.pop()
                if not stack:
                    result.append(''.join(quoted))
            elif char in pairs:
                stack.append(pairs[char])
    result.append(''.join(quoted) if stack else replace_names(''.join(plain), mapping, known, ambiguous))
    return ''.join(result)
