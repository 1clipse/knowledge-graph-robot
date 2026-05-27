"""Citation verification — check that [P1], [P2] markers in answers
are supported by the referenced knowledge graph paths.

Rule-based lightweight checks (no extra LLM call):
1. Entity names mentioned in the sentence must appear in the cited path
2. Path reference numbers must be valid
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

_CITATION_RE = re.compile(r"\[P(\d+)\]")


@dataclass
class CitationResult:
    marker: str = ""
    path_index: int = 0
    snippet: str = ""
    status: str = "ok"  # "ok", "invalid_ref", "unsupported_entity", "empty_path"
    issues: List[str] = field(default_factory=list)


class CitationVerifier:
    """Verify that citations in LLM answers are supported by retrieved paths."""

    def verify(
        self,
        answer: str,
        paths: List[Dict[str, Any]],
    ) -> List[CitationResult]:
        """Verify all citations in an answer against the provided paths.

        Args:
            answer: The LLM-generated answer text with [P1], [P2] markers
            paths: List of path dicts from GraphRagRetriever, each with
                   'nodes' (list of {labels, properties}) and 'edges'

        Returns:
            List of CitationResult, one per unique citation marker found.
        """
        results: List[CitationResult] = []
        seen: Set[int] = set()

        for match in _CITATION_RE.finditer(answer):
            idx = int(match.group(1)) - 1  # 0-based
            if idx in seen:
                continue
            seen.add(idx)

            result = CitationResult(
                marker=f"P{idx + 1}",
                path_index=idx,
                snippet=self._extract_snippet(answer, match.start(), match.end()),
            )

            # Check 1: Is the path reference valid?
            if idx < 0 or idx >= len(paths):
                result.status = "invalid_ref"
                result.issues.append(f"Path {idx + 1} does not exist (only {len(paths)} paths available)")
                results.append(result)
                continue

            path = paths[idx]
            if not path:
                result.status = "empty_path"
                result.issues.append(f"Path {idx + 1} is empty or null")
                results.append(result)
                continue

            # Check 2: Do entities in the sentence appear in the path?
            sentence = result.snippet
            path_nodes = self._extract_path_entities(path)

            entity_issues = self._check_entity_support(sentence, path_nodes, idx)
            if entity_issues:
                result.status = "unsupported_entity"
                result.issues.extend(entity_issues)
            else:
                result.status = "ok"

            results.append(result)

        return results

    @staticmethod
    def _extract_snippet(answer: str, start: int, end: int) -> str:
        """Extract text snippet around a citation marker."""
        s = max(0, start - 60)
        e = min(len(answer), end + 60)
        return answer[s:e].strip()

    @staticmethod
    def _extract_path_entities(path: Dict[str, Any]) -> Set[str]:
        """Extract all entity names from a path's nodes."""
        names: Set[str] = set()
        for node in path.get("nodes", []):
            props = node.get("properties", {})
            name = props.get("name", "")
            if name:
                names.add(name.lower())
        return names

    def _check_entity_support(
        self, sentence: str, path_names: Set[str], path_idx: int
    ) -> List[str]:
        """Check if named entities in the sentence appear in the path.

        Simple rule: any word that looks like a known entity name
        (3+ chars, capital letter or CJK) should be in the path.
        """
        issues: List[str] = []
        # Extract potential entity mentions: words with 3+ chars
        # that could be entity names (mixed alpha/num, CJK, hyphens)
        potential_entities = re.findall(
            r'[A-Z][\w\-]{2,}|[一-鿿]{2,6}',
            sentence,
        )
        checked: Set[str] = set()
        for token in potential_entities:
            token_lower = token.lower()
            if token_lower in checked:
                continue
            checked.add(token_lower)
            # Check if this token or its substrings appear in path entities
            found = any(
                token_lower in pname or pname in token_lower
                for pname in path_names
            )
            if not found:
                # Only flag tokens that look like entity names (not common words)
                if len(token) >= 3 and not self._is_common_word(token):
                    issues.append(
                        f"Entity '{token}' in sentence near [P{path_idx + 1}] "
                        f"not found in path entities: {sorted(path_names)[:10]}"
                    )
        return issues

    # Common CJK characters that rarely appear in entity names alone
    _CJK_STOP_CHARS: Set[str] = set(
        "的了为是在有和与及或对从到被把让给向朝于由因以而能会可"
        "要就也都不但只仅还又更很太最极非每各某其之这那此该"
        "个种次回度倍率性化体量值数点线面位方式法"
    )

    @classmethod
    def _is_common_word(cls, text: str) -> bool:
        """Heuristic: filter out common non-entity words and CJK stop characters."""
        common_en = {
            "THE", "AND", "FOR", "HAS", "ITS", "CAN", "ALL", "NOT",
            "THIS", "THAT", "WITH", "FROM", "WHEN", "WILL", "WHAT",
            "WHICH", "THEIR", "ABOUT", "THERE", "WOULD", "COULD",
        }
        upper = text.upper()
        if upper in common_en:
            return True
        # Pure CJK token composed entirely of stop characters
        if all('一' <= ch <= '鿿' for ch in text):
            if all(ch in cls._CJK_STOP_CHARS for ch in text):
                return True
        return False

    def summarize(self, results: List[CitationResult]) -> Dict[str, Any]:
        """Produce a summary of citation verification results."""
        total = len(results)
        ok = sum(1 for r in results if r.status == "ok")
        invalid = sum(1 for r in results if r.status == "invalid_ref")
        unsupported = sum(1 for r in results if r.status == "unsupported_entity")
        return {
            "total_citations": total,
            "verified": ok,
            "invalid_refs": invalid,
            "unsupported_entities": unsupported,
            "details": [r for r in results if r.status != "ok"],
        }
