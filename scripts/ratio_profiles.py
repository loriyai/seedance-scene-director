"""Unified storyboard video-ratio profiles.

The profile registry owns only the stable routing contract between a saved
video ratio and the storyboard rules that apply to it.  Creative wording
remains in ``references/storyboard-rules.md``; this module never generates or
rewrites shot prose.

Historical units may have no ``ratio_profile`` field.  They remain readable
and render through their saved output policy without being silently migrated.
"""
import copy
import hashlib
import json


POLICY = "storyboard-ratio-profile-v1"
VERSION = 1

_PROFILES = {
    "16:9": {
        "video_ratio": "16:9",
        "profile_id": "landscape-v1",
        "profile_version": VERSION,
        "label": "横屏16:9",
        "orientation": "landscape",
        "rules_anchor": "references/storyboard-rules.md#横屏16:9配置",
    },
    "9:16": {
        "video_ratio": "9:16",
        "profile_id": "portrait-v1",
        "profile_version": VERSION,
        "label": "竖屏9:16",
        "orientation": "portrait",
        "rules_anchor": "references/storyboard-rules.md#竖屏9:16配置",
    },
}

CHOICES = {"1": "16:9", "2": "9:16"}


def _digest(value):
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def validate_ratio(value):
    if value not in _PROFILES:
        raise ValueError("视频比例须为16:9或9:16")
    return value


def profile_for_ratio(ratio):
    """Return a defensive copy of the profile selected by ``ratio``."""
    validate_ratio(ratio)
    profile = copy.deepcopy(_PROFILES[ratio])
    profile["policy"] = POLICY
    profile["profile_digest"] = _digest(profile)
    return profile


def profile_id_for_ratio(ratio):
    return profile_for_ratio(ratio)["profile_id"]


def profile_for_unit(unit, *, allow_legacy=True):
    """Resolve and validate a unit's frozen profile.

    A missing profile is accepted only for historical units.  New delivery
    code calls ``require_unit_profile`` after assigning one explicitly.
    """
    ratio = unit.get("video_ratio")
    if ratio is None:
        return None
    profile = profile_for_ratio(ratio)
    saved = unit.get("ratio_profile")
    if saved is None and allow_legacy:
        return profile
    if saved != profile["profile_id"]:
        raise ValueError("分镜比例配置与视频比例不一致")
    version = unit.get("ratio_profile_version")
    if version is not None and version != profile["profile_version"]:
        raise ValueError("分镜比例配置版本不兼容")
    digest = unit.get("ratio_profile_digest")
    if digest is not None and digest != profile["profile_digest"]:
        raise ValueError("分镜比例配置摘要不一致")
    return profile


def require_unit_profile(unit):
    """Require the frozen profile fields used by newly delivered units."""
    ratio = unit.get("video_ratio")
    if ratio is None:
        raise ValueError("新分镜单元缺少视频比例")
    profile = profile_for_ratio(ratio)
    if unit.get("ratio_profile") != profile["profile_id"]:
        raise ValueError("新分镜单元缺少与视频比例匹配的画幅配置")
    if unit.get("ratio_profile_version") != profile["profile_version"]:
        raise ValueError("新分镜单元缺少有效的画幅配置版本")
    if unit.get("ratio_profile_digest") != profile["profile_digest"]:
        raise ValueError("新分镜单元缺少有效的画幅配置摘要")
    return profile


def apply_to_unit(unit, ratio):
    """Attach the current profile snapshot to a newly authored unit."""
    profile = profile_for_ratio(ratio)
    unit.update(video_ratio=ratio,
                ratio_profile=profile["profile_id"],
                ratio_profile_version=profile["profile_version"],
                ratio_profile_digest=profile["profile_digest"])
    return profile


def profile_summary(ratio):
    profile = profile_for_ratio(ratio)
    return {key: profile[key] for key in
            ("video_ratio", "profile_id", "profile_version", "label",
             "orientation", "rules_anchor", "profile_digest", "policy")}
