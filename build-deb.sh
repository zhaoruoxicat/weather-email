#!/usr/bin/env bash
# 一键构建 deb 包
#
# 用法： ./build-deb.sh

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# /tmp 常为小容量 tmpfs，把临时目录指到项目内
export TMPDIR="$HERE/.buildtmp"
mkdir -p "$TMPDIR"

echo "==> 编译检查"
PY="${PYTHON:-python3}"
"$PY" -m compileall -q "$HERE/weatheremail" || {
    echo "编译失败，已中止" >&2; exit 1
}

echo "==> 运行自测"
mkdir -p "$HERE/.testtmp"
( cd "$HERE" && TMPDIR="$HERE/.testtmp" "$PY" tests/selftest.py ) || {
    echo "自测未通过，已中止打包" >&2; exit 1
}

echo "==> 清理字节码"
find "$HERE/weatheremail" -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true

echo "==> 构建 deb"
make -C "$HERE/packaging" clean >/dev/null 2>&1 || true
make -C "$HERE/packaging"

echo "==> 验证 deb"
"$HERE/verify-deb.sh" "$(ls -t "$HERE"/packaging/build/*.deb | head -1)"

echo ""
echo "完成。安装："
echo "    sudo dpkg -i $(ls -t "$HERE"/packaging/build/*.deb | head -1)"
echo ""
