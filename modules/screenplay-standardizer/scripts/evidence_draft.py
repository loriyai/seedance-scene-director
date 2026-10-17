#!/usr/bin/env python3
"""Prepare source-bound, single-line evidence before freezing a candidate.

This is a formatting adapter, not a semantic reviewer or a ledger migration.
It accepts explicit contiguous source references, preserves their original text,
and never reads or rewrites decision receipts, review flags, or ledger hashes.
"""

import argparse
import copy
import json
import re
from pathlib import Path

from asset_review import fingerprint


POLICY = "source-evidence-draft-v1"


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def read_source(path):
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    records = data.get("paragraphs") if isinstance(data, dict) else None
    _require(isinstance(records, list) and bool(records), "source.json须有非空paragraphs数组")
    _require(all(isinstance(item, dict) and type(item.get("id")) is int
                 and item["id"] == index and isinstance(item.get("text"), str)
                 for index, item in enumerate(records, 1)), "source.json段落须为连续编号的原始文字")
    return [item["text"] for item in records]


def _record(paragraphs, selection):
    required = {"evidence_id", "source_paragraphs"}
    _require(isinstance(selection, dict) and required <= set(selection)
             and set(selection) <= required | {"quote"}, "证据选择只接受evidence_id/source_paragraphs及可选逐字quote")
    eid, refs = selection["evidence_id"], selection["source_paragraphs"]
    _require(isinstance(eid, str) and bool(re.fullmatch(r"[A-Za-z0-9_-]+", eid)), "evidence_id须为非空字母数字或下划线/短横线标记")
    _require(isinstance(refs, list) and bool(refs) and all(type(ref) is int for ref in refs)
             and refs == list(range(refs[0], refs[-1] + 1))
             and 1 <= refs[0] <= refs[-1] <= len(paragraphs), f"{eid}来源段落须有效、连续、有序且无重复")
    raw_paragraphs = [{"id": ref, "text": paragraphs[ref - 1]} for ref in refs]
    raw_range = "\n".join(item["text"] for item in raw_paragraphs)
    quote = selection.get("quote", raw_range)
    _require(isinstance(quote, str) and bool(quote.strip()), f"{eid}原始摘录须为非空原文")
    matches = [match.start() for match in re.finditer(f"(?={re.escape(quote)})", raw_range)]
    _require(len(matches) == 1, f"{eid}摘录须在所引连续原文中逐字且唯一出现；跨段使用换行分隔")
    start, end = matches[0], matches[0] + len(quote)
    # Do not permit a broad range to disguise a quote from only one endpoint.
    # Empty paragraphs inside a truly spanning quote remain in the raw evidence.
    first_end = len(raw_paragraphs[0]["text"])
    last_start = len(raw_range) - len(raw_paragraphs[-1]["text"])
    _require(bool(raw_range[start:min(end, first_end)].strip())
             and bool(raw_range[max(start, last_start):end].strip()),
             f"{eid}摘录须实际覆盖首尾所引段落；缩小范围或选取完整连续摘录")
    single_line = " ".join(quote.split())
    compact_quote, compact_range = "".join(quote.split()), "".join(raw_range.split())
    _require(len(list(re.finditer(f"(?={re.escape(compact_quote)})", compact_range))) == 1,
             f"{eid}摘录移除空白后仍须唯一定位；扩大逐字摘录或缩小段落范围")
    record = {"evidence_id": eid, "source_paragraphs": copy.deepcopy(refs),
              "evidence": single_line, "raw_evidence": quote,
              "raw_paragraphs": raw_paragraphs}
    record["binding_sha256"] = fingerprint(record)
    return record


def prepare_evidence_draft(paragraphs, selections, *, source_ref):
    """Create a new catalog from explicitly selected ranges; infer no facts."""
    _require(isinstance(paragraphs, list) and bool(paragraphs)
             and all(isinstance(text, str) for text in paragraphs), "原文须为非空段落文字数组")
    _require(isinstance(source_ref, str) and bool(source_ref.strip()), "须保留source_ref原文文件引用")
    _require(isinstance(selections, list) and bool(selections), "证据选择须为非空数组")
    records = [_record(paragraphs, selection) for selection in selections]
    _require(len({item["evidence_id"] for item in records}) == len(records), "evidence_id不得重复")
    result = {"policy": POLICY, "source_ref": source_ref,
              "source_digest": fingerprint(paragraphs), "records": records,
              "semantic_review": "not_performed", "formal_ready": False}
    result["binding_sha256"] = fingerprint(result)
    return result


def verify_evidence_draft(paragraphs, draft):
    """Recheck original ranges and emitted text against the actual source."""
    expected = {"policy", "source_ref", "source_digest", "records", "semantic_review", "formal_ready", "binding_sha256"}
    _require(isinstance(draft, dict) and set(draft) == expected and draft["policy"] == POLICY,
             "不支持的证据草稿结构")
    _require(draft["source_digest"] == fingerprint(paragraphs), "证据草稿的原文摘要已变化；不得刷新旧审阅摘要")
    _require(isinstance(draft["records"], list) and bool(draft["records"]), "证据草稿缺少记录")
    selections = []
    for record in draft["records"]:
        _require(isinstance(record, dict) and set(record) == {
            "evidence_id", "source_paragraphs", "evidence", "raw_evidence", "raw_paragraphs", "binding_sha256"}, "证据草稿记录结构无效")
        selections.append({"evidence_id": record["evidence_id"], "source_paragraphs": record["source_paragraphs"],
                           "quote": record["raw_evidence"]})
    current = prepare_evidence_draft(paragraphs, selections, source_ref=draft["source_ref"])
    _require(current == draft, "证据文字、原始段落、绑定摘要或未审阅状态不一致")
    return {"status": "evidence_format_checked", "source_digest": draft["source_digest"],
            "binding_sha256": draft["binding_sha256"], "evidence_ids": [item["evidence_id"] for item in draft["records"]],
            "semantic_review": "not_performed", "formal_ready": False}


def evidence_fields(paragraphs, draft, evidence_id):
    """Return existing ledger evidence fields only, after source verification."""
    verify_evidence_draft(paragraphs, draft)
    found = [record for record in draft["records"] if record["evidence_id"] == evidence_id]
    _require(len(found) == 1, f"找不到唯一证据：{evidence_id}")
    return {key: copy.deepcopy(found[0][key]) for key in ("evidence", "source_paragraphs")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare", help="在冻结候选前，从显式原文范围生成新的单行证据草稿")
    prepare.add_argument("source", type=Path)
    prepare.add_argument("selections", type=Path, help="evidence_id/source_paragraphs及可选逐字quote的JSON数组")
    prepare.add_argument("--output", type=Path, required=True)
    check = sub.add_parser("check", help="只读核对草稿与实际原文；不作语义批准")
    check.add_argument("source", type=Path)
    check.add_argument("draft", type=Path)
    args = parser.parse_args()
    try:
        paragraphs = read_source(args.source)
        if args.command == "prepare":
            selections = json.loads(args.selections.read_text(encoding="utf-8-sig"))
            result = prepare_evidence_draft(paragraphs, selections, source_ref=str(args.source.resolve()))
            report = verify_evidence_draft(paragraphs, result)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            # Exclusive creation also protects source, selections and all old
            # evidence/review artifacts if the caller chooses an existing path.
            with args.output.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
            report["output"] = str(args.output.resolve())
        else:
            draft = json.loads(args.draft.read_text(encoding="utf-8-sig"))
            report = verify_evidence_draft(paragraphs, draft)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.exit(1, f"错误：{exc}\n")


if __name__ == "__main__":
    main()
