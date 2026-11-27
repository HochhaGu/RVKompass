"""Prüfungen am echten Goldstandard eval/goldstandard.csv (P12)."""

import hashlib
from pathlib import Path

from app.config import Config
from app.services import evaluation, gold_merge

ROOT = Path(__file__).resolve().parent.parent
GOLD_PATH = ROOT / "eval" / "goldstandard.csv"
OPEN_PATH = ROOT / "eval" / "goldstandard_offen.csv"

# SHA-256 der Kopfzeile und der 97 Anfragen vor P12 (git show HEAD:eval/goldstandard.csv auf
# Stand fd6f0d3, vor dem Anhängen der Erweiterung 2026-11)
OLD_PART_LINES = 98
OLD_PART_SHA256 = "1168e06afb1887a93f6becdc07c265672eff302821bbf6a71f4423ff98fb1682"


def _entries():
    return evaluation.parse_gold(GOLD_PATH.read_text(encoding="utf-8").splitlines())


def test_old_part_is_byte_identical():
    data = GOLD_PATH.read_bytes()
    old_part = b"".join(data.splitlines(keepends=True)[:OLD_PART_LINES])
    assert hashlib.sha256(old_part).hexdigest() == OLD_PART_SHA256


def test_queries_are_unique():
    keys = [gold_merge.query_key(e.query) for e in _entries()]
    duplicates = sorted({k for k in keys if keys.count(k) > 1})
    assert duplicates == []


def test_only_allowed_types():
    types = {e.typ for e in _entries()}
    assert types <= set(evaluation.ALLOWED_TYPES), types - set(evaluation.ALLOWED_TYPES)


def test_all_notations_formally_valid():
    assert gold_merge.format_errors(_entries()) == []


def test_subset_sizes_match_file():
    entries = _entries()
    sizes = Config.GOLD_SUBSET_SIZES
    assert list(sizes) == ["alt", "neu_technik", "neu_andere"]
    assert sizes["alt"] == 97
    assert sum(sizes.values()) == len(entries)
    labels = evaluation.subset_labels(len(entries), sizes)
    first_other = labels.index("neu_andere")
    assert entries[first_other].query == "Mietrecht"
    assert entries[first_other - 1].query == "Firmware"


def test_open_file_consistent_if_present():
    if not OPEN_PATH.exists():
        return
    gold_lines = GOLD_PATH.read_text(encoding="utf-8").splitlines()
    open_lines = OPEN_PATH.read_text(encoding="utf-8").splitlines()
    assert open_lines[0] == gold_lines[0]
    open_keys = {
        gold_merge.query_key(e.query) for e in evaluation.parse_gold(open_lines)
    }
    gold_keys = {gold_merge.query_key(e.query) for e in _entries()}
    assert open_keys.isdisjoint(gold_keys)
