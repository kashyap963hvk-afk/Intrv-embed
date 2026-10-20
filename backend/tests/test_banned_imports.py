"""CLAUDE.md rule 8: no emotion, expression, identity or demographic analysis in the backend.

Emotion recognition in recruitment is prohibited in the EU (AI Act Art. 5(1)(f)), and biometric categorisation
(Art. 5(1)(g)) too. Integrity signals such as face count and gaze direction are computed in the candidate's
browser only (ADR 0005). This test fails if a face/emotion/biometric library is imported or declared.
"""

from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BANNED_MODULES = {
    "deepface",
    "fer",
    "face_recognition",
    "dlib",
    "mediapipe",
    "insightface",
    "facenet_pytorch",
    "feat",  # py-feat (facial expression / action units)
    "hsemotion",
    "emotiefflib",
    "retinaface",
    "mtcnn",
}
BANNED_PACKAGES = {
    "deepface",
    "fer",
    "face-recognition",
    "dlib",
    "mediapipe",
    "insightface",
    "facenet-pytorch",
    "py-feat",
    "hsemotion",
    "emotiefflib",
    "retina-face",
    "mtcnn",
}
BANNED_CALLS = re.compile(r"CascadeClassifier|face_detection|FaceDetector|FaceLandmarker|blendshape", re.IGNORECASE)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module.split(".")[0])
    return found


def _sources() -> list[Path]:
    return [p for folder in ("app", "workers") for p in (ROOT / folder).rglob("*.py")]


def test_no_banned_imports() -> None:
    offenders = {str(p.relative_to(ROOT)): sorted(_imports(p) & BANNED_MODULES) for p in _sources()}
    assert {k: v for k, v in offenders.items() if v} == {}


def test_no_face_or_expression_analysis_calls() -> None:
    offenders = [str(p.relative_to(ROOT)) for p in _sources() if BANNED_CALLS.search(p.read_text(encoding="utf-8"))]
    assert offenders == []


def _normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def test_no_banned_dependencies() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    declared = [
        *project["project"].get("dependencies", []),
        *[d for group in project.get("dependency-groups", {}).values() for d in group if isinstance(d, str)],
    ]
    names = {_normalise(re.split(r"[<>=!~;\[ ]", d, maxsplit=1)[0]) for d in declared}
    locked = {
        _normalise(m.group(1))
        for m in re.finditer(r'^name = "([^"]+)"', (ROOT / "uv.lock").read_text(encoding="utf-8"), re.MULTILINE)
    }
    assert (names | locked) & BANNED_PACKAGES == set()
