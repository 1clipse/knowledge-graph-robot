#!/usr/bin/env python3
"""
Auto-clean labeled training data.

Fixes:
  1. Remove CAD primitives as entities (圆/圆弧/线段/图块...)
  2. Deduplicate reversed/duplicate relations
  3. Fix contains relation direction (parent -> child)
  4. Normalize vague entity names (4-M -> M4 threaded hole)
  5. Flag noise entities (中心标记/尺寸标注 as Process)
  6. Drop orphaned relations after entity removal
  7. Re-score quality and report changes

Usage:
  python clean_labeled.py                          # clean default file
  python clean_labeled.py --input custom.json      # clean a specific file
  python clean_labeled.py --dry-run                # preview only, no save
"""

from __future__ import annotations

import json
import re
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any, Optional

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_HERE = Path(__file__).resolve().parent
_DEFAULT_INPUT = _HERE / "data" / "enhanced_labeled.json"


# CAD primitives that are NOT real components
CAD_PRIMITIVES = {
    "圆", "圆弧", "线段", "多段线", "椭圆", "样条曲线", "点",
    "图块/组件", "图块", "组件", "标注文字", "尺寸标注", "注释",
    "中心线", "中心标记", "中心标记符号", "剖面线",
}

# Vague material names
VAGUE_MATERIALS = {
    "标准材料", "标准件", "金属", "钢材", "非金属",
}

# Vague process names (annotation elements mis-labeled as Process)
NOISE_PROCESSES = {
    "中心标记", "中心标记符号", "尺寸信息", "尺寸标注",
    "标注文字", "注释", "图块", "图层",
}

# Entity name normalization rules: pattern -> replacement
NAME_NORMALIZE = [
    # Threaded holes: "6-M14" -> "M14螺纹孔", "4-M" -> "M4螺纹孔"
    (re.compile(r"^(\d+)-M(\d+)$"), lambda m: f"M{m.group(2)}螺纹孔"),
    (re.compile(r"^(\d+)-M$"),    lambda m: f"M{m.group(1)}螺纹孔"),
    # Remove trailing whitespace
    (re.compile(r"\s+$"), ""),
    (re.compile(r"^\s+"), ""),
]


def normalize_name(name: str) -> str:
    for pattern, replacement in NAME_NORMALIZE:
        if callable(replacement):
            name = pattern.sub(replacement, name)
        else:
            name = pattern.sub(replacement, name)
    return name


def is_cad_primitive(entity: dict) -> bool:
    name = entity.get("name", "")
    etype = entity.get("type", "")
    if name in CAD_PRIMITIVES:
        return True
    return False


def is_noise_entity(entity: dict) -> bool:
    name = entity.get("name", "")
    etype = entity.get("type", "")
    if etype == "Material" and name in VAGUE_MATERIALS:
        return True
    if etype == "Process" and name in NOISE_PROCESSES:
        return True
    return False


def relation_key(r: dict) -> tuple:
    """Canonical key for dedup — order-independent for symmetric types"""
    src = r.get("source", {}).get("name", "")
    tgt = r.get("target", {}).get("name", "")
    rtype = r.get("relation_type", "")
    return (src, tgt, rtype)


def fix_relation_direction(r: dict, entity_names: set) -> bool:
    """Fix contains/performs_process direction. Returns True if changed."""
    src = r.get("source", {})
    tgt = r.get("target", {})
    rtype = r.get("relation_type", "")

    src_name = src.get("name", "")
    tgt_name = tgt.get("name", "")

    if rtype == "contains":
        src_small = bool(re.search(r"[M\d]+", src_name)) or len(src_name) <= 6
        tgt_big = any(kw in tgt_name for kw in ["法兰", "涡轮箱", "底板", "底座", "手臂", "机身"])
        if src_small and tgt_big:
            r["source"], r["target"] = r["target"], r["source"]
            return True

    if rtype == "performs_process":
        if tgt.get("type") == "Component" and src.get("type") == "Process":
            r["source"], r["target"] = r["target"], r["source"]
            return True

    return False


def clean_sample(sample: dict) -> tuple[dict, list[str], list[str]]:
    """Clean a single sample. Returns (cleaned_sample, fixes, warnings)."""
    fixes = []
    warnings = []

    try:
        output = json.loads(sample["output"])
    except (json.JSONDecodeError, KeyError):
        warnings.append("JSON parse failed, skipping")
        return sample, fixes, warnings

    entities = output.get("entities", [])
    relations = output.get("relations", [])

    # Track changes for report
    removed_entities = []
    removed_relations = []
    renamed = []

    # Step 1: Normalize entity names
    for e in entities:
        old_name = e.get("name", "")
        new_name = normalize_name(old_name)
        if new_name != old_name:
            renamed.append(f"'{old_name}' -> '{new_name}'")
            e["name"] = new_name
            # Update relations referencing this entity
            for r in relations:
                if r.get("source", {}).get("name") == old_name:
                    r["source"]["name"] = new_name
                if r.get("target", {}).get("name") == old_name:
                    r["target"]["name"] = new_name

    # Step 2: Remove CAD primitives
    clean_entities = []
    for e in entities:
        if is_cad_primitive(e):
            removed_entities.append(f"CAD primitive: '{e.get('name')}' ({e.get('type')})")
        elif is_noise_entity(e):
            removed_entities.append(f"noise entity: '{e.get('name')}' ({e.get('type')})")
        else:
            clean_entities.append(e)
    entities = clean_entities

    # Step 3: Remove orphaned relations (references to removed entities)
    entity_names = {e["name"] for e in entities}
    clean_relations = []
    for r in relations:
        src_name = r.get("source", {}).get("name", "")
        tgt_name = r.get("target", {}).get("name", "")
        if src_name not in entity_names:
            removed_relations.append(f"orphan source '{src_name}' -> '{tgt_name}'")
            continue
        if tgt_name not in entity_names:
            removed_relations.append(f"orphan target '{src_name}' -> '{tgt_name}'")
            continue
        clean_relations.append(r)
    relations = clean_relations

    # Step 4: Fix relation direction
    for r in relations:
        if fix_relation_direction(r, entity_names):
            fixes.append(
                f"reversed relation: {r.get('source',{}).get('name','')} "
                f"-[{r.get('relation_type','')}]-> "
                f"{r.get('target',{}).get('name','')}"
            )

    # Step 5: Dedup relations (remove exact duplicates + reversed duplicates)
    seen = set()
    deduped = []
    for r in relations:
        rk = relation_key(r)
        # Also check reversed
        rk_rev = (rk[1], rk[0], rk[2])
        if rk in seen or rk_rev in seen:
            removed_relations.append(f"duplicate: {rk[0]} -[{rk[2]}]-> {rk[1]}")
            continue
        seen.add(rk)
        # For symmetric types, also block the reverse
        if rk[2] in ("contains",):
            seen.add(rk_rev)
        deduped.append(r)
    relations = deduped

    # Rebuild output
    new_output = json.dumps(
        {"entities": entities, "relations": relations},
        ensure_ascii=False,
    )

    # Collect all changes
    for name in renamed:
        fixes.append(f"renamed: {name}")
    for name in removed_entities:
        fixes.append(f"removed entity: {name}")
    for name in removed_relations:
        fixes.append(f"removed relation: {name}")

    # Update sample
    cleaned = deepcopy(sample)
    cleaned["output"] = new_output

    # Update _meta
    meta = cleaned.get("_meta", {})
    meta["cleaned"] = True
    meta["entity_count"] = len(entities)
    meta["relation_count"] = len(relations)

    # Re-score
    warnings_after = meta.get("warnings", [])
    if not entities:
        meta["quality"] = "review"
    elif len(entities) >= 3 and len(relations) >= 2 and len(removed_relations) == 0:
        meta["quality"] = "good"
    elif len(entities) >= 1 and len(relations) >= 1:
        meta["quality"] = "ok"
    else:
        meta["quality"] = "review"

    # Add cleaning log
    meta.setdefault("cleaning_log", []).extend(fixes)

    cleaned["_meta"] = meta
    return cleaned, fixes, warnings


def main(input: str = None, dry_run: bool = False):
    inpath = Path(input) if input else _DEFAULT_INPUT

    if not inpath.exists():
        print(f"File not found: {inpath}")
        return

    with open(inpath, "r", encoding="utf-8") as f:
        samples = json.load(f)

    print(f"Input:  {inpath} ({len(samples)} samples)")
    print(f"Mode:   {'DRY RUN (preview only)' if dry_run else 'CLEAN + SAVE'}")
    print()

    total_fixes = 0
    cleaned_samples = []

    for i, sample in enumerate(samples):
        source = sample.get("_meta", {}).get("source_file", f"sample_{i}")
        old_quality = sample.get("_meta", {}).get("quality", "?")

        cleaned, fixes, warnings = clean_sample(sample)

        new_quality = cleaned.get("_meta", {}).get("quality", "?")
        quality_mark = ""
        if old_quality != new_quality:
            quality_mark = f"  quality: {old_quality} -> {new_quality}"

        if fixes:
            total_fixes += len(fixes)
            print(f"[{source}] {len(fixes)} fixes{quality_mark}")
            for fix in fixes:
                print(f"  - {fix}")
        elif warnings:
            print(f"[{source}] {len(warnings)} warnings (no fixes applied)")
            for w in warnings:
                print(f"  ! {w}")
        else:
            print(f"[{source}] clean, no issues")

        cleaned_samples.append(cleaned)
        print()

    # Summary
    print(f"{'='*60}")
    print(f"Total: {total_fixes} fixes across {len(samples)} samples")

    scores = {"good": 0, "ok": 0, "review": 0}
    total_e, total_r = 0, 0
    for s in cleaned_samples:
        m = s.get("_meta", {})
        scores[m.get("quality", "review")] += 1
        total_e += m.get("entity_count", 0)
        total_r += m.get("relation_count", 0)
    print(f"Quality: good={scores['good']}, ok={scores['ok']}, review={scores['review']}")
    print(f"Entities: {total_e} total ({total_e/max(len(cleaned_samples),1):.1f}/sample)")
    print(f"Relations: {total_r} total ({total_r/max(len(cleaned_samples),1):.1f}/sample)")

    if not dry_run:
        outpath = inpath.with_name(inpath.stem + "_cleaned.json")
        with open(outpath, "w", encoding="utf-8") as f:
            json.dump(cleaned_samples, f, ensure_ascii=False, indent=2)
        print(f"\nSaved: {outpath}")
    else:
        print("\n[DRY RUN] No changes written. Remove --dry-run to save.")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Auto-clean labeled training data")
    parser.add_argument("--input", type=str, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    main(input=args.input, dry_run=args.dry_run)
