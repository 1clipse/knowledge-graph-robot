#!/usr/bin/env python3
"""
Train spaCy NER model for industrial robot knowledge graph entity extraction.

Usage:
    # Train from real labeled documents (recommended, no Neo4j needed)
    python scripts/train_spacy_ner.py --no-kg --eval

    # Train from a specific data directory
    python scripts/train_spacy_ner.py --data-dir scripts/finetune/data --no-kg --eval

    # Train with KG fallback
    python scripts/train_spacy_ner.py --output models/kg_robot_ner
    python scripts/train_spacy_ner.py --iterations 50
    python scripts/train_spacy_ner.py --eval
"""
from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger

from extractors.spacy_extractor import (
    ENTITY_TYPE_TO_LABEL,
    LABEL_TO_ENTITY_TYPE,
    generate_ner_training_data,
    train_spacy_model,
)


def evaluate_model(nlp, test_data: list) -> dict:
    """Evaluate NER model: precision, recall, F1 per entity type."""
    from spacy.training import Example
    from collections import defaultdict

    scores: dict = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0})

    for text, annotations in test_data:
        doc = nlp(text)
        example = Example.from_dict(nlp.make_doc(text), annotations)
        # Gold entities
        gold_ents = {(e.start_char, e.end_char): e.label_ for e in example.reference.ents}
        # Predicted entities
        pred_ents = {(e.start_char, e.end_char): e.label_ for e in example.predicted.ents}

        all_labels = set(gold_ents.values()) | set(pred_ents.values())

        for label in all_labels:
            gold_set = {k for k, v in gold_ents.items() if v == label}
            pred_set = {k for k, v in pred_ents.items() if v == label}
            tp = len(gold_set & pred_set)
            fp = len(pred_set - gold_set)
            fn = len(gold_set - pred_set)
            scores[label]["tp"] += tp
            scores[label]["fp"] += fp
            scores[label]["fn"] += fn

    results = {}
    for label, s in scores.items():
        precision = s["tp"] / (s["tp"] + s["fp"]) if (s["tp"] + s["fp"]) > 0 else 0.0
        recall = s["tp"] / (s["tp"] + s["fn"]) if (s["tp"] + s["fn"]) > 0 else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
        entity_type = LABEL_TO_ENTITY_TYPE.get(label, label)
        results[entity_type] = {
            "label": label,
            "precision": round(precision, 3),
            "recall": round(recall, 3),
            "f1": round(f1, 3),
            "support": s["tp"] + s["fn"],
        }

    # Macro average
    if results:
        macro_p = sum(r["precision"] for r in results.values()) / len(results)
        macro_r = sum(r["recall"] for r in results.values()) / len(results)
        macro_f1 = sum(r["f1"] for r in results.values()) / len(results)
        results["macro_avg"] = {
            "precision": round(macro_p, 3),
            "recall": round(macro_r, 3),
            "f1": round(macro_f1, 3),
        }

    return results


def main():
    parser = argparse.ArgumentParser(description="Train spaCy NER for Industrial Robot KG")
    parser.add_argument("--output", default="models/kg_robot_ner", help="Output model path")
    parser.add_argument("--base-model", default="zh_core_web_lg", help="Base spaCy model")
    parser.add_argument("--iterations", type=int, default=30, help="Training iterations")
    parser.add_argument("--eval", action="store_true", help="Evaluate after training")
    parser.add_argument("--test-split", type=float, default=0.2, help="Test split ratio")
    parser.add_argument(
        "--data-dir",
        default="",
        help="Path to directory containing labeled JSON and raw text files for training",
    )
    parser.add_argument(
        "--no-kg",
        action="store_true",
        help="Skip KG connection (use only local data files, recommended if Neo4j not running)",
    )
    args = parser.parse_args()

    client = None
    if not args.no_kg:
        try:
            from config.settings import get_config
            from graph.client import Neo4jClient

            config = get_config()
            client = Neo4jClient(
                uri=config.neo4j.uri,
                username=config.neo4j.username,
                password=config.neo4j.password,
                database=config.neo4j.database,
            )
            logger.info(f"Connected to Neo4j: {config.neo4j.uri}")
        except Exception as e:
            logger.warning(f"Neo4j unavailable ({e}), using local data files only")
            client = None

    # Generate training data from real documents (or KG fallback)
    logger.info("Generating training data...")
    train_data = generate_ner_training_data(
        neo4j_client=client,
        data_dir=args.data_dir,
    )

    if not train_data:
        logger.error("No training data available.")
        logger.info("Falling back: training with synthetic data only")
        train_data = _get_synthetic_data()

    logger.info(f"Total training examples: {len(train_data)}")

    # Split train/test
    random.shuffle(train_data)
    split_idx = int(len(train_data) * (1 - args.test_split))
    train_set = train_data[:split_idx]
    test_set = train_data[split_idx:] if args.eval else []

    logger.info(f"Train: {len(train_set)}, Test: {len(test_set)}")

    # Train
    logger.info(f"Training for {args.iterations} iterations...")
    nlp = train_spacy_model(
        train_data=train_set,
        output_path=args.output,
        iterations=args.iterations,
        base_model=args.base_model,
    )

    logger.info(f"Model saved to {args.output}")

    # Evaluate
    if args.eval and test_set:
        logger.info("Evaluating model...")
        results = evaluate_model(nlp, test_set)
        print("\n" + "=" * 60)
        print("  NER Evaluation Results")
        print("=" * 60)
        print(f"{'Entity Type':<25} {'P':>8} {'R':>8} {'F1':>8} {'Support':>8}")
        print("-" * 60)
        for etype, r in sorted(results.items()):
            if etype == "macro_avg":
                print("-" * 60)
                print(f"{'** MACRO AVG **':<25} {r['precision']:>8.3f} {r['recall']:>8.3f} {r['f1']:>8.3f}")
            else:
                print(f"{etype:<25} {r['precision']:>8.3f} {r['recall']:>8.3f} {r['f1']:>8.3f} {r['support']:>8}")
        print("=" * 60)

    if client:
        client.close()
    logger.info("Done.")


def _get_synthetic_data() -> list:
    """Fallback synthetic training data when KG is empty."""
    data = []
    for entity_type, label in ENTITY_TYPE_TO_LABEL.items():
        examples = [
            (f"{entity_type}是工业机器人领域的核心概念。",
             {"entities": [(0, len(entity_type), label)]}),
        ]
        data.extend(examples)
    return data


if __name__ == "__main__":
    main()
