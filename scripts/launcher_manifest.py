"""Build and verify standalone launcher provenance; never touches numerical targets."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys


APPROVED = {
    "python/lumafem/gui/assets/lumafem.png": "918e360f5d8bcf8e36926ad37441fc6f07d915880e0a748d3feb600d0d4856a4",
    "python/lumafem/gui/assets/lumafem.ico": "e9d8895491156b29fda600d04c5fe873d5ab67dbd3e279e1b42ca79db039ac8b",
}
FIXED_INPUTS = tuple(APPROVED) + (
    "python/lumafem/gui/assets/provenance.json",
    "scripts/launcher_manifest.py", "scripts/build.ps1",
)
MANIFEST_NAME = "launcher-build-manifest.json"


def record(path: Path) -> dict:
    """Return content identity, rejecting missing files and symbolic redirects."""
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Required regular file is missing or redirected: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return {"sha256": digest.hexdigest(), "size": path.stat().st_size}


def run(arguments: list[str], directory: Path) -> str:
    """Execute trusted absolute build tools with explicit failure diagnostics."""
    environment = dict(os.environ, VSLANG="1033")
    result = subprocess.run(arguments, cwd=directory, env=environment, capture_output=True, check=False)
    output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
    if result.returncode:
        raise ValueError(f"Command failed ({result.returncode}): {arguments!r}\n{output}")
    return output.strip()


def cache_values(build: Path) -> dict[str, str]:
    values = {}
    for line in (build / "CMakeCache.txt").read_text(encoding="utf-8").splitlines():
        if line and not line.startswith(("//", "#")) and "=" in line:
            name, value = line.split("=", 1)
            values[name.split(":", 1)[0]] = value
    return values


def tool_paths(root: Path, build: Path) -> dict[str, Path]:
    cache = cache_values(build)
    expected_source = (root / "tools/windows_launcher").resolve()
    if Path(cache.get("CMAKE_HOME_DIRECTORY", "")).resolve() != expected_source:
        raise ValueError("Launcher cache belongs to another source tree")
    if cache.get("CMAKE_GENERATOR") != "Ninja" or cache.get("CMAKE_BUILD_TYPE") != "Release":
        raise ValueError("Launcher cache must use Ninja Release")
    if cache.get("CMAKE_PROJECT_NAME") != "LumaFEMWindowsLauncher":
        raise ValueError("Not the standalone launcher build")
    keys = {"compiler": "CMAKE_CXX_COMPILER", "resource_compiler": "CMAKE_RC_COMPILER",
            "cmake": "CMAKE_COMMAND", "ninja": "CMAKE_MAKE_PROGRAM"}
    paths = {}
    for name, key in keys.items():
        value = cache.get(key, "")
        if not value or not Path(value).is_absolute():
            raise ValueError(f"Missing absolute tool path in cache: {key}")
        path = Path(value).resolve()
        record(path)
        paths[name] = path
    # Use only this repository's configured build tools, never caller PATH tools.
    for name in ("cmake", "ninja"):
        expected = {(root / ".venv/Scripts" / (name + ".exe")).resolve()}
        # The PyPI CMake entry is a shim; CMAKE_COMMAND names its real binary.
        if name == "cmake":
            expected.add((root / ".venv/Lib/site-packages/cmake/data/bin/cmake.exe").resolve())
        if paths[name] not in expected:
            raise ValueError(f"Unexpected {name} executable: {paths[name]}")
    return paths


def pe_identity(path: Path) -> dict:
    """Parse the actual PE COFF machine and optional-header GUI subsystem."""
    data = path.read_bytes()
    if len(data) < 64 or data[:2] != b"MZ":
        raise ValueError("Launcher is not a DOS/PE executable")
    offset = struct.unpack_from("<I", data, 0x3C)[0]
    if offset + 24 + 70 > len(data) or data[offset:offset + 4] != b"PE\0\0":
        raise ValueError("Launcher PE header is missing or truncated")
    machine = struct.unpack_from("<H", data, offset + 4)[0]
    optional_size = struct.unpack_from("<H", data, offset + 20)[0]
    optional = offset + 24
    if optional_size < 70 or optional + optional_size > len(data):
        raise ValueError("Launcher PE optional header is truncated")
    magic = struct.unpack_from("<H", data, optional)[0]
    subsystem = struct.unpack_from("<H", data, optional + 68)[0]
    if (machine, magic, subsystem) != (0x8664, 0x20B, 2):
        raise ValueError("Launcher must be an x64 PE32+ Windows GUI executable")
    return {"machine": machine, "optional_header_magic": magic, "subsystem": subsystem}


def input_inventory(root: Path) -> dict:
    directory = root / "tools/windows_launcher"
    if not directory.is_dir() or directory.is_symlink():
        raise ValueError("Standalone launcher source directory is missing or redirected")
    paths = list(directory.rglob("*"))
    if any(path.is_symlink() for path in paths):
        raise ValueError("Launcher source inventory contains a symbolic redirect")
    names = {path.relative_to(root).as_posix() for path in paths if path.is_file()}
    required = {"tools/windows_launcher/CMakeLists.txt", "tools/windows_launcher/launcher.cpp",
                "tools/windows_launcher/launcher.rc.in"}
    if not required.issubset(names):
        raise ValueError("Launcher source inventory is incomplete")
    names.update(FIXED_INPUTS)
    inventory = {name: record(root / name) for name in sorted(names)}
    for name, approved in APPROVED.items():
        if inventory[name]["sha256"] != approved:
            raise ValueError(f"Unapproved artwork content: {name}")
    return inventory


def snapshot(root: Path, build: Path, paths: dict[str, Path]) -> dict:
    """Collect current content and clean-graph evidence for strict comparison."""
    inputs = input_inventory(root)
    clean = run([str(paths["ninja"]), "-n", "LumaFEM"], build)
    if clean != "ninja: no work to do.":
        raise ValueError(f"Launcher build is not clean; rebuild it first:\n{clean}")
    executable = build / "bin/LumaFEM.exe"
    tools = {}
    for name, path in paths.items():
        arguments = [str(path), "--version"] if name in ("cmake", "ninja") else [str(path), "/?"]
        tools[name] = {"path": str(path), **record(path),
                       "identity": run(arguments, build)}
    return {
        "version": 1,
        "project_root": str(root), "build_dir": str(build),
        "inputs": inputs, "approved_artwork": APPROVED,
        "configured_resource": {"path": "launcher.rc", **record(build / "launcher.rc")},
        "executable": {"path": "bin/LumaFEM.exe", **record(executable), **pe_identity(executable)},
        "build_graph": {name: record(build / name) for name in ("CMakeCache.txt", "build.ninja")},
        "tools": tools,
        "clean_check": {"arguments": [str(paths["ninja"]), "-n", "LumaFEM"], "output": clean},
        "build_command": [str(paths["cmake"]), "--build", str(build), "--target", "LumaFEM", "--clean-first"],
        "scope": "Launcher build provenance only; not a signature or scientific qualification",
    }


def main() -> int:
    """Write after a successful owned build, or reject any provenance drift."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("write", "verify"))
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--build-dir", required=True, type=Path)
    arguments = parser.parse_args()
    try:
        root, build = arguments.project_root.resolve(), arguments.build_dir.resolve()
        if root == build or root / "tools/windows_launcher" == build:
            raise ValueError("Launcher build directory must be separate from source")
        paths = tool_paths(root, build)
        manifest_path = build / MANIFEST_NAME
        if arguments.mode == "write":
            # Recreate only this independent target: a replaced but newer PE must
            # never be blessed by an otherwise successful no-op build.
            input_inventory(root)
            run([str(paths["cmake"]), "--build", str(build), "--target", "LumaFEM", "--clean-first"], build)
            paths = tool_paths(root, build)
            manifest = snapshot(root, build, paths)
            temporary = manifest_path.with_suffix(".json.tmp")
            try:
                temporary.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
                os.replace(temporary, manifest_path)
            finally:
                temporary.unlink(missing_ok=True)
        else:
            record(manifest_path)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(manifest, dict) or manifest.get("version") != 1:
                raise ValueError("Launcher manifest must be a version 1 object")
            current = snapshot(root, build, paths)
            if manifest != current:
                changed = sorted(key for key in set(manifest) | set(current) if manifest.get(key) != current.get(key))
                raise ValueError(f"Launcher manifest mismatch ({', '.join(changed)}); rebuild using scripts/build.ps1")
        print(f"Launcher manifest {arguments.mode}: OK ({manifest_path})")
        return 0
    except (OSError, ValueError, KeyError, struct.error, subprocess.SubprocessError) as error:
        print(f"Launcher manifest {arguments.mode} failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
