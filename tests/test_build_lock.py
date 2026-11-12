"""Sperrdatei-Erzeugung (scripts/build_lock.py) – ohne pip, mit erfundenen Metadaten."""

import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "build_lock", ROOT / "scripts" / "build_lock.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_lock"] = module
    spec.loader.exec_module(module)
    return module


build_lock = _load()
Info = build_lock.PackageInfo

LINUX = {
    "implementation_name": "cpython",
    "implementation_version": "3.12.3",
    "os_name": "posix",
    "platform_machine": "x86_64",
    "platform_release": "",
    "platform_system": "Linux",
    "platform_version": "",
    "python_full_version": "3.12.3",
    "platform_python_implementation": "CPython",
    "python_version": "3.12",
    "sys_platform": "linux",
}

PACKAGES = {
    "flask": Info("Flask", "3.0.3", ("Werkzeug>=3.0", "blinker>=1.6")),
    "werkzeug": Info("Werkzeug", "3.1.9", ("MarkupSafe>=2.1.1",)),
    "markupsafe": Info("MarkupSafe", "3.0.3", ()),
    "blinker": Info("blinker", "1.9.0", ()),
    "tool": Info(
        "tool",
        "1.0",
        (
            'colorama; sys_platform == "win32"',
            'pytest; extra == "test"',
            "typing_extensions",
        ),
    ),
    "typing-extensions": Info("typing_extensions", "4.16.0", ()),
    "colorama": Info("colorama", "0.4.6", ()),
}


def lookup(name):
    return PACKAGES.get(build_lock.canonical(name))


def test_closure_contains_sub_dependencies_sorted():
    lines, missing = build_lock.dependency_closure(["Flask"], lookup, LINUX)
    assert missing == []
    assert lines == [
        "blinker==1.9.0",
        "Flask==3.0.3",
        "MarkupSafe==3.0.3",
        "Werkzeug==3.1.9",
    ]


def test_markers_for_other_platform_and_extras_are_skipped():
    lines, _ = build_lock.dependency_closure(["tool"], lookup, LINUX)
    assert lines == ["tool==1.0", "typing_extensions==4.16.0"]
    windows = dict(LINUX, sys_platform="win32")
    lines, _ = build_lock.dependency_closure(["tool"], lookup, windows)
    assert "colorama==0.4.6" in lines
    assert not any(line.startswith("pytest") for line in lines)


def test_missing_packages_are_reported():
    lines, missing = build_lock.dependency_closure(["Flask", "gunicorn"], lookup, LINUX)
    assert missing == ["gunicorn"]
    assert "Flask==3.0.3" in lines


def test_dev_lock_only_has_extra_packages():
    runtime, dev_only, missing = build_lock.build(["Flask"], ["tool"], lookup, LINUX)
    assert missing == []
    assert "Flask==3.0.3" in runtime
    assert dev_only == ["tool==1.0", "typing_extensions==4.16.0"]


def test_read_requirement_names_skips_includes_and_comments(tmp_path):
    path = tmp_path / "req.txt"
    path.write_text("-r other.txt\n# Kommentar\nFlask==3.0.3\n\ngunicorn  # neu\n")
    assert build_lock.read_requirement_names(path) == ["Flask", "gunicorn"]


def test_lock_files_have_exact_versions_only():
    for name in ("requirements.lock", "requirements-dev.lock"):
        for line in (ROOT / name).read_text(encoding="utf-8").splitlines():
            if not line or line.startswith("#") or line == "-r requirements.lock":
                continue
            assert re.fullmatch(r"[A-Za-z0-9._-]+==[A-Za-z0-9.+!-]+", line), line


def test_runtime_lock_has_cpu_torch_and_no_cuda():
    lines = (ROOT / "requirements.lock").read_text(encoding="utf-8").splitlines()
    torch = [line for line in lines if line.startswith("torch==")]
    assert torch and torch[0].endswith("+cpu")
    assert not any(line.lower().startswith("nvidia-") for line in lines)
