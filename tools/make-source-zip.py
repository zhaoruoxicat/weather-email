#!/usr/bin/env python3
"""把源码树打包成干净的 zip（排除构建产物与缓存）。

用法： python3 tools/make-source-zip.py [输出目录]

默认输出到项目根的 dist/ 下，文件名带版本号。
"""

from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 排除规则：目录名、文件名模式
EXCLUDE_DIRS = {
    "__pycache__",
    ".git",
    ".workbuddy",
    ".venv",
    "build",      # packaging/build 下的 deb 等产物
    "dist",
    ".testtmp",
    ".buildtmp",
    ".verify",
    ".Trash-0",
}
EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".deb", ".log", ".tmp"}
EXCLUDE_NAMES = {".DS_Store", "Thumbs.db", ".stamp"}
# samples/ 下的截图是验证时生成的临时产物（PNG 已压缩，占体积且非源码），
# 只保留 HTML 邮件示例本身
EXCLUDE_PNG_DIRS = {"samples"}


def read_version() -> str:
    text = (ROOT / "weatheremail" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    return m.group(1) if m else "0.0.0"


def should_exclude(path: Path) -> bool:
    rel = path.relative_to(ROOT)
    if any(part in EXCLUDE_DIRS for part in rel.parts):
        return True
    if path.suffix in EXCLUDE_SUFFIXES:
        return True
    if path.name in EXCLUDE_NAMES:
        return True
    # 排除 samples 下的截图
    if path.suffix == ".png" and rel.parts and rel.parts[0] in EXCLUDE_PNG_DIRS:
        return True
    return False


def main() -> int:
    version = read_version()
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist"
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / f"weatheremail-{version}-src.zip"

    files: list[Path] = []
    for path in sorted(ROOT.rglob("*")):
        if path.is_dir():
            continue
        if should_exclude(path):
            continue
        files.append(path)

    if not files:
        print("没有可打包的文件", file=sys.stderr)
        return 1

    # 统一用 DEFLATE，源码文本压缩率高
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in files:
            # 顶层套一层目录，解压后不会散落一地
            arcname = f"weatheremail-{version}/" + str(path.relative_to(ROOT))
            zf.write(path, arcname)

    print(f"已生成：{zip_path}")
    print(f"文件数：{len(files)}")
    print(f"体积　：{zip_path.stat().st_size / 1024:.1f} KB")
    print()
    print("包内清单：")
    for path in files:
        print(f"  {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
