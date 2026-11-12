"""Erzeugt die Sperrdateien requirements.lock und requirements-dev.lock (P8).

Grundlage sind die Pakete, die in der laufenden Python-Umgebung installiert sind
(`importlib.metadata`), samt aller Unterabhängigkeiten. Es wird nichts installiert und kein pip
benutzt. Marker (z. B. `sys_platform == "win32"`) werden für die laufende Plattform ausgewertet,
Zweige mit `extra == …` fallen weg.

Aufruf (in der .venv, in der auch pytest läuft):
    python scripts/build_lock.py            # schreibt beide Lock-Dateien
    python scripts/build_lock.py --check    # zeigt nur an, was fehlt oder abweicht

Fehlt ein Paket aus requirements*.txt in der Umgebung (z. B. ein neu ergänztes), bricht das
Skript mit einer Meldung ab. Mit `--allow-missing` wird es stattdessen als Kommentarzeile
„# FEHLT …“ vermerkt; die Lock-Datei ist dann unvollständig und muss nach der Installation neu
erzeugt werden. `--assume NAME==VERSION` (mit `--assume-requires`) übernimmt ein Paket so, wie
angegeben – nur für geprüfte Angaben gedacht.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

from packaging.markers import default_environment
from packaging.requirements import InvalidRequirement, Requirement

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class PackageInfo:
    name: str  # Schreibweise laut Metadaten, z. B. "Flask"
    version: str
    requires: tuple[str, ...]  # Requires-Dist-Zeilen


MetadataLookup = Callable[[str], PackageInfo | None]


def canonical(name: str) -> str:
    """PEP 503: "Flask_Limiter" -> "flask-limiter"."""
    return re.sub(r"[-_.]+", "-", name).lower()


def requirement_applies(req: Requirement, environment: dict | None = None) -> bool:
    """Gilt die Abhängigkeit auf dieser Plattform ohne Extras?"""
    if req.marker is None:
        return True
    env = dict(environment or default_environment())
    env["extra"] = ""
    return bool(req.marker.evaluate(env))


def dependency_closure(
    top_level: Iterable[str],
    metadata_lookup: MetadataLookup,
    environment: dict | None = None,
) -> tuple[list[str], list[str]]:
    """Alle Pakete samt Unterabhängigkeiten als sortierte "name==version"-Zeilen.

    Zweiter Rückgabewert: Namen, die `metadata_lookup` nicht kennt (nicht installiert).
    """
    seen: dict[str, PackageInfo] = {}
    missing: list[str] = []
    queue = [canonical(n) for n in top_level]
    while queue:
        key = queue.pop()
        if key in seen or key in missing:
            continue
        info = metadata_lookup(key)
        if info is None:
            missing.append(key)
            continue
        seen[key] = info
        for line in info.requires:
            try:
                req = Requirement(line)
            except InvalidRequirement:
                continue
            if requirement_applies(req, environment):
                queue.append(canonical(req.name))
    lines = sorted(
        (f"{info.name}=={info.version}" for info in seen.values()),
        key=lambda s: canonical(s.split("==", 1)[0]),
    )
    return lines, sorted(missing)


def installed_lookup(name: str) -> PackageInfo | None:
    """Metadaten eines installierten Pakets aus der laufenden Umgebung."""
    try:
        dist = metadata.distribution(name)
    except metadata.PackageNotFoundError:
        return None
    return PackageInfo(
        name=dist.metadata["Name"] or name,
        version=dist.version,
        requires=tuple(dist.requires or ()),
    )


def read_requirement_names(path: Path) -> list[str]:
    """Paketnamen aus einer requirements-Datei; "-r …", Kommentare und Leerzeilen fallen weg."""
    names = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        names.append(Requirement(line).name)
    return names


def with_assumed(
    lookup: MetadataLookup, assumed: dict[str, PackageInfo]
) -> MetadataLookup:
    def combined(name: str) -> PackageInfo | None:
        return lookup(name) or assumed.get(canonical(name))

    return combined


def render_lock(lines: list[str], header: Iterable[str]) -> str:
    return "\n".join([*header, *lines]) + "\n"


HEADER = (
    "# Automatisch erzeugt von scripts/build_lock.py – nicht von Hand ändern.",
    "# Exakte Versionen aller Laufzeitpakete samt Unterabhängigkeiten (Python {py}, {plat}).",
)
DEV_HEADER = (
    "# Automatisch erzeugt von scripts/build_lock.py – nicht von Hand ändern.",
    "# Entwicklungswerkzeuge zusätzlich zu requirements.lock (Python {py}, {plat}).",
    "-r requirements.lock",
)


def build(
    runtime_names: list[str],
    dev_names: list[str],
    lookup: MetadataLookup,
    environment: dict | None = None,
) -> tuple[list[str], list[str], list[str]]:
    """(Laufzeit-Zeilen, nur-Entwicklung-Zeilen, fehlende Pakete)."""
    runtime, missing_rt = dependency_closure(runtime_names, lookup, environment)
    dev_all, missing_dev = dependency_closure(
        [*runtime_names, *dev_names], lookup, environment
    )
    runtime_set = set(runtime)
    dev_only = [line for line in dev_all if line not in runtime_set]
    return runtime, dev_only, sorted(set(missing_rt) | set(missing_dev))


def parse_assumed(values: list[str], requires: list[str]) -> dict[str, PackageInfo]:
    result = {}
    for value in values:
        name, _, version = value.partition("==")
        if not name or not version:
            raise SystemExit(f"--assume erwartet NAME==VERSION, nicht {value!r}")
        result[canonical(name)] = PackageInfo(name, version, tuple(requires))
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="nichts schreiben")
    parser.add_argument(
        "--allow-missing",
        action="store_true",
        help="fehlende Pakete nur als Kommentar vermerken statt abzubrechen",
    )
    parser.add_argument("--assume", action="append", default=[])
    parser.add_argument("--assume-requires", action="append", default=[])
    args = parser.parse_args(argv)

    runtime_names = read_requirement_names(ROOT / "requirements.txt")
    dev_names = read_requirement_names(ROOT / "requirements-dev.txt")
    lookup = with_assumed(
        installed_lookup, parse_assumed(args.assume, args.assume_requires)
    )
    runtime, dev_only, missing = build(runtime_names, dev_names, lookup)

    env = default_environment()
    fmt = {"py": env["python_full_version"], "plat": env["platform_machine"]}
    print(f"Laufzeit: {len(runtime)} Pakete, nur Entwicklung: {len(dev_only)} Pakete")
    for line in runtime:
        if canonical(line.split("==")[0]) in {"torch", "torchvision", "torchaudio"}:
            print(f"Hinweis: {line}")
    cuda = [line for line in runtime if canonical(line).startswith("nvidia-")]
    if cuda:
        print(f"Hinweis: {len(cuda)} CUDA-Pakete (nvidia-*) in der Laufzeit-Hülle")
    if missing:
        print("Nicht installiert: " + ", ".join(missing), file=sys.stderr)
        if not args.allow_missing:
            return 1
    if args.check:
        return 0 if not missing else 1
    header = [h.format(**fmt) for h in HEADER]
    header += [
        f"# FEHLT (nicht installiert, Version offen): {name}" for name in missing
    ]
    (ROOT / "requirements.lock").write_text(
        render_lock(runtime, header), encoding="utf-8"
    )
    (ROOT / "requirements-dev.lock").write_text(
        render_lock(dev_only, [h.format(**fmt) for h in DEV_HEADER]), encoding="utf-8"
    )
    print("Geschrieben: requirements.lock, requirements-dev.lock")
    return 0


if __name__ == "__main__":
    sys.exit(main())
