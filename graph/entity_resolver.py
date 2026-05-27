"""EntityResolver — unifies entity name normalization, alias expansion, and
fuzzy deduplication for both ingest and query paths.

Usage:
    resolver = EntityResolver()
    resolved = resolver.resolve("FANUC", "Manufacturer")
    # => CanonicalEntity(canonical="FANUC", type="Manufacturer", ...)

    # Batch resolve an entire ExtractionResult before writing
    result = resolver.resolve_result(result)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import yaml
from loguru import logger

_ALIASES_PATH = Path(__file__).resolve().parent.parent / "config" / "entity_aliases.yaml"


@dataclass
class DuplicateCandidate:
    entity_a: str
    entity_b: str
    type_a: str
    type_b: str
    score: float
    reason: str
    suggested_action: str = "manual_review"


@dataclass
class ResolveResult:
    canonical: str
    original: str
    type: str
    resolved_from: str = ""  # "alias", "fuzzy", "self", "model_norm"
    same_as_candidates: List[str] = field(default_factory=list)
    duplicate_candidates: List[DuplicateCandidate] = field(default_factory=list)


class EntityResolver:
    """Resolve entity names to canonical forms using aliases and fuzzy matching."""

    def __init__(self, aliases_path: Optional[str] = None) -> None:
        self._aliases_path = aliases_path or str(_ALIASES_PATH)
        self._alias_map: Dict[str, Dict[str, str]] = {}  # type_lower -> {variant_lower: canonical}
        self._canonical_set: Dict[str, Set[str]] = {}     # type_lower -> {canonical_lower, ...}
        self._load_aliases()

    def _load_aliases(self) -> None:
        try:
            with open(self._aliases_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except FileNotFoundError:
            logger.warning(f"Entity aliases file not found: {self._aliases_path}")
            return

        for entry in data.get("aliases", []):
            canonical = entry["canonical"]
            etype = entry["type"]
            type_key = etype.lower()
            if type_key not in self._alias_map:
                self._alias_map[type_key] = {}
                self._canonical_set[type_key] = set()
            self._canonical_set[type_key].add(canonical.lower())
            self._alias_map[type_key][canonical.lower()] = canonical
            for alias in entry.get("aliases", []):
                self._alias_map[type_key][alias.lower()] = canonical

        logger.info(
            f"EntityResolver: loaded {sum(len(m) for m in self._alias_map.values())} aliases "
            f"across {len(self._alias_map)} types"
        )

    def _normalize_name(self, name: str) -> str:
        """Normalize a name for comparison (model number normalization)."""
        name = name.strip()
        # Collapse multiple spaces
        name = re.sub(r"\s+", " ", name)
        return name

    def _normalize_for_match(self, name: str) -> str:
        """More aggressive normalization for fuzzy matching:
        remove spaces, hyphens, and lowercase."""
        n = name.lower().strip()
        n = re.sub(r"[\s\-]", "", n)
        return n

    def resolve(self, name: str, entity_type: str) -> ResolveResult:
        """Resolve a single entity name to its canonical form.

        Resolution order:
        1. Exact canonical match → self
        2. Alias lookup → canonical
        3. Model number normalization → canonical
        4. Returns self (no match found)
        """
        original = name
        name = self._normalize_name(name)
        type_key = entity_type.lower()

        # 1. Already canonical?
        canonicals = self._canonical_set.get(type_key, set())
        if name.lower() in canonicals:
            return ResolveResult(canonical=name, original=original, type=entity_type, resolved_from="self")

        # 2. Alias lookup
        alias_targets = self._alias_map.get(type_key, {})
        if name.lower() in alias_targets:
            canonical = alias_targets[name.lower()]
            return ResolveResult(canonical=canonical, original=original, type=entity_type, resolved_from="alias")

        # 3. Model number normalization (e.g. "IRB-6700" ↔ "IRB6700")
        norm_name = self._normalize_for_match(name)
        for variant, canonical in alias_targets.items():
            if self._normalize_for_match(variant) == norm_name:
                return ResolveResult(canonical=canonical, original=original, type=entity_type, resolved_from="model_norm")

        # 4. No match — return self
        return ResolveResult(canonical=name, original=original, type=entity_type, resolved_from="self")

    def resolve_result(self, result) -> Any:
        """Resolve all entity names in an ExtractionResult.

        Entities are renamed to canonical form; relations are updated
        when their source/target names are changed.
        """
        # Build name mapping: (type, old_name) -> new_name
        name_map: Dict[Tuple[str, str], str] = {}

        for entity in result.entities:
            resolved = self.resolve(entity.name, entity.type)
            if resolved.canonical != entity.name:
                name_map[(entity.type, entity.name)] = resolved.canonical
                entity.name = resolved.canonical

        for rel in result.relations:
            src_key = (rel.source.type, rel.source.name)
            tgt_key = (rel.target.type, rel.target.name)
            if src_key in name_map:
                rel.source.name = name_map[src_key]
            if tgt_key in name_map:
                rel.target.name = name_map[tgt_key]

        return result

    def find_duplicate_candidates(
        self,
        entities: List[tuple],  # List of (name, type)
        threshold: float = 0.85,
    ) -> List[DuplicateCandidate]:
        """Find potential duplicate entities using normalized name comparison.

        Args:
            entities: List of (name, type) tuples
            threshold: Fuzzy score threshold for reporting duplicates

        Returns a list of DuplicateCandidate objects.
        """
        candidates: List[DuplicateCandidate] = []
        norm_map: Dict[str, List[tuple]] = {}  # normalized_name -> [(name, type)]

        for name, etype in entities:
            norm = self._normalize_for_match(name)
            if norm not in norm_map:
                norm_map[norm] = []
            norm_map[norm].append((name, etype))

        # Same normalized name but different original names → duplicate
        for norm_name, entries in norm_map.items():
            if len(entries) < 2:
                continue
            original_names = {e[0] for e in entries}
            if len(original_names) < 2:
                continue
            types_present = {e[1] for e in entries}
            names_list = list(original_names)
            for i in range(len(names_list)):
                for j in range(i + 1, len(names_list)):
                    reason = (
                        "same_normalized_name_different_types"
                        if len(types_present) > 1
                        else "same_normalized_name"
                    )
                    candidates.append(DuplicateCandidate(
                        entity_a=names_list[i],
                        entity_b=names_list[j],
                        type_a=entries[0][1],
                        type_b=entries[-1][1] if len(types_present) > 1 else entries[0][1],
                        score=1.0,
                        reason=reason,
                        suggested_action="auto_merge" if len(types_present) == 1 else "manual_review",
                    ))

        # Fuzzy matching across names (for non-exact-normalized matches)
        try:
            from rapidfuzz import fuzz
            seen_pairs: Set[tuple] = set()
            for i in range(len(entities)):
                for j in range(i + 1, len(entities)):
                    name_i, type_i = entities[i]
                    name_j, type_j = entities[j]
                    if type_i != type_j:
                        continue
                    pair = (min(name_i, name_j), max(name_i, name_j))
                    if pair in seen_pairs:
                        continue
                    seen_pairs.add(pair)
                    score = fuzz.ratio(name_i.lower(), name_j.lower()) / 100.0
                    if score >= threshold and score < 0.98:  # exclude exact matches
                        candidates.append(DuplicateCandidate(
                            entity_a=name_i,
                            entity_b=name_j,
                            type_a=type_i,
                            type_b=type_j,
                            score=score,
                            reason="fuzzy_match",
                            suggested_action="manual_review",
                        ))
        except ImportError:
            pass

        # Sort by score descending
        candidates.sort(key=lambda c: -c.score)
        return candidates
