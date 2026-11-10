"""Build and inspect the portable Windows executable; never include user data.

From a clean Windows x64 Python 3.12 venv:
    python -m pip install -r requirements-build.txt
    python scripts/build_windows.py --version 1.0.0

Only dist/TelegramAutomation-Windows-x64.exe is a distribution artifact.
"""
import argparse
import hashlib
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
NAME = "TelegramAutomation-Windows-x64"


def version_tuple(value):
    if not re.fullmatch(r"\d+\.\d+\.\d+", value):
        raise argparse.ArgumentTypeError("Version must be three numbers, such as 1.0.0")
    parts = tuple(int(part) for part in value.split("."))
    if any(part > 65535 for part in parts):
        raise argparse.ArgumentTypeError("Version components must not exceed 65535")
    return parts


def write_resources(folder, version):
    numbers = (*version_tuple(version), 0)
    version_file = folder / "version-info.txt"
    version_file.write_text(
        "VSVersionInfo(\n"
        f"  ffi=FixedFileInfo(filevers={numbers!r}, prodvers={numbers!r}, "
        "mask=0x3f, flags=0, OS=0x40004, fileType=1, subtype=0, date=(0, 0)),\n"
        "  kids=[StringFileInfo([StringTable('040904B0', [\n"
        "    StringStruct('CompanyName', 'Marco0431'),\n"
        "    StringStruct('FileDescription', 'Telegram Automation'),\n"
        f"    StringStruct('FileVersion', {version!r}),\n"
        "    StringStruct('InternalName', 'TelegramAutomation'),\n"
        f"    StringStruct('OriginalFilename', {NAME + '.exe'!r}),\n"
        "    StringStruct('ProductName', 'Telegram Automation'),\n"
        f"    StringStruct('ProductVersion', {version!r})\n"
        "  ])]), VarFileInfo([VarStruct('Translation', [1033, 1200])])]\n"
        ")\n", encoding="utf-8")
    manifest = folder / "app.manifest"
    template = (ROOT / "packaging/windows.manifest").read_text(encoding="utf-8")
    manifest.write_text(template.replace('version="1.0.0.0"', f'version="{version}.0"'), encoding="utf-8")
    return version_file, manifest


def verify_executable(path):
    """Fail the build if packaging drops DPI settings or adds private/project files."""
    import pefile
    from PyInstaller.archive.readers import CArchiveReader

    pe = pefile.PE(str(path))
    try:
        if pe.FILE_HEADER.Machine != 0x8664 or pe.OPTIONAL_HEADER.Subsystem != 2:
            raise RuntimeError("Expected a Windows x64 GUI executable")
        manifests = []
        for entry in pe.DIRECTORY_ENTRY_RESOURCE.entries:
            if entry.id == 24:  # RT_MANIFEST
                for resource in entry.directory.entries:
                    for language in resource.directory.entries:
                        item = language.data.struct
                        manifests.append(pe.get_data(item.OffsetToData, item.Size))
        if len(manifests) != 1:
            raise RuntimeError("Executable must contain one application manifest")
        manifest = ET.fromstring(manifests[0])
        values = {node.tag.rsplit("}", 1)[-1]: node for node in manifest.iter()}
        if "PerMonitorV2" not in (values["dpiAwareness"].text or ""):
            raise RuntimeError("The high DPI manifest was not embedded")
        if values["requestedExecutionLevel"].attrib.get("level") != "asInvoker":
            raise RuntimeError("Portable executable must not request administrator rights")
    finally:
        pe.close()

    archive = CArchiveReader(str(path))
    for member in archive.toc:
        normalized = member.replace("\\", "/").lower()
        basename = normalized.rsplit("/", 1)[-1]
        if (basename in ("config.json", "app.log", ".env") or ".session" in basename
                or normalized.startswith(("docs/", "tests/"))):
            raise RuntimeError(f"Unexpected private/development file in package: {member}")
    bundled_names = {member.replace("\\", "/").rsplit("/", 1)[-1].lower() for member in archive.toc}
    system_directory = Path(os.environ["SystemRoot"]) / "System32"
    for member in archive.toc:
        if not member.lower().endswith((".dll", ".pyd")):
            continue
        dependency = pefile.PE(data=archive.extract(member), fast_load=True)
        try:
            dependency.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"]])
            for item in getattr(dependency, "DIRECTORY_ENTRY_IMPORT", []):
                name = item.dll.decode("ascii").lower()
                if (name not in bundled_names and not name.startswith(("api-ms-win-", "ext-ms-win-"))
                        and not (system_directory / name).exists()):
                    raise RuntimeError(f"Unbundled DLL dependency: {member} requires {name}")
        finally:
            dependency.close()
    python_archive = archive.open_embedded_archive("PYZ.pyz")
    if any(name == "tests" or name.startswith("test_") for name in python_archive.toc):
        raise RuntimeError("Application test modules must not be bundled")
    for module in ("config", "dpi", "display", "engine", "ui", "openai", "telethon", "pystray._win32"):
        if module not in python_archive.toc:
            raise RuntimeError(f"Missing runtime module: {module}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default="1.0.0")
    args = parser.parse_args()
    version_tuple(args.version)
    if sys.platform != "win32" or struct.calcsize("P") != 8:
        parser.error("Build on Windows with a 64-bit Python interpreter")
    if sys.version_info[:2] != (3, 12):
        parser.error("The locked build environment uses Python 3.12")
    folder = ROOT / "build/windows"
    folder.mkdir(parents=True, exist_ok=True)
    version_file, manifest = write_resources(folder, args.version)
    environment = dict(os.environ, PYINSTALLER_CONFIG_DIR=str(folder / "cache"))
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--onefile", "--windowed", "--noupx", "--icon", "NONE",
        "--name", NAME, "--distpath", str(ROOT / "dist"),
        "--workpath", str(folder / "work"), "--specpath", str(folder),
        "--manifest", str(manifest), "--version-file", str(version_file),
        "--collect-submodules", "openai", "--copy-metadata", "openai",
        "--hidden-import", "pystray._win32",
        "--hidden-import", "python_socks.async_.asyncio",
        str(ROOT / "main.py"),
    ], cwd=ROOT, env=environment, check=True)
    executable = ROOT / "dist" / (NAME + ".exe")
    digest = verify_executable(executable)
    print(f"Validated {executable}\nSize: {executable.stat().st_size:,} bytes\nSHA256: {digest}")


if __name__ == "__main__":
    main()
