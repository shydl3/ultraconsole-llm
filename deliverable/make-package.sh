#!/usr/bin/env bash
#
# 把 package/ 模板、compose、文档和镜像 tar 组装成可直接发给 Windows 用户的目录与 zip。
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
DELIVERABLE="$ROOT/deliverable"
VERSION="$(tr -d '[:space:]' < "$DELIVERABLE/VERSION")"
TAG="${TAG:-$VERSION}"
IMAGE="${IMAGE:-ultraconsole}"
NAME="UltraConsole-${TAG}-Win64"
OUT="$DELIVERABLE/dist/$NAME"
ZIP="$DELIVERABLE/dist/${NAME}.zip"

rm -rf "$OUT"
mkdir -p "$OUT"/{images,models/detect,data,offline,docs}

cp -a "$DELIVERABLE/package/." "$OUT/"
rm -f "$OUT/env.example"
cp -a "$DELIVERABLE/compose/docker-compose.yml" "$OUT/docker-compose.yml"
cp -a "$DELIVERABLE"/docs/*.md "$OUT/docs/"
cp -a "$ROOT/LICENSE" "$OUT/LICENSE"
cp -a "$DELIVERABLE/VERSION" "$OUT/VERSION.txt"

sed "s/__TAG__/${TAG}/g" "$DELIVERABLE/package/env.example" > "$OUT/.env"

if [[ -f "$DELIVERABLE/dist/images/${IMAGE}-${TAG}.tar.gz" ]]; then
  cp -a "$DELIVERABLE/dist/images/${IMAGE}-${TAG}.tar.gz" "$OUT/images/"
else
  echo "警告：未找到镜像 tar，交付包将只有启动脚本。可先运行 build.sh。" >&2
fi

# zip 不保留空目录；放占位文件确保解压后目录结构完整。
for dir in images models models/detect data offline; do
  : > "$OUT/$dir/.keep"
done

# YOLO11 姿态权重随包内置，避免大陆网络下载 GitHub。
YOLO_SRC="$ROOT/backend/models/yolo11m-pose.pt"
if [[ -f "$YOLO_SRC" ]]; then
  cp -a "$YOLO_SRC" "$OUT/models/detect/yolo11m-pose.pt"
else
  echo "警告：未找到 $YOLO_SRC，交付包不会包含 YOLO11 权重。" >&2
fi

# Windows 友好：文本转 CRLF；PowerShell/说明文件加 UTF-8 BOM（PowerShell 5.1 需要）。
python3 - "$OUT" <<'PY'
import sys
from pathlib import Path

root = Path(sys.argv[1])
bom = b"\xef\xbb\xbf"
crlf_suffixes = {".bat", ".ps1", ".txt", ".md", ".yml", ".env"}
bom_suffixes = {".ps1", ".txt"}

for path in sorted(root.rglob("*")):
    if not path.is_file():
        continue
    is_env = path.name == ".env"
    if not is_env and path.suffix.lower() not in crlf_suffixes:
        continue
    data = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    if path.suffix.lower() in bom_suffixes and not data.startswith(bom):
        data = bom + data
    path.write_bytes(data)
PY

python3 - "$OUT" "$ZIP" <<'PY'
import sys
import zipfile
from pathlib import Path

source = Path(sys.argv[1])
target = Path(sys.argv[2])
target.parent.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
    for path in sorted(source.rglob("*")):
        if path.is_file():
            archive.write(path, path.relative_to(source.parent))
PY

if command -v sha256sum >/dev/null 2>&1; then
  (cd "$DELIVERABLE/dist" && sha256sum "${NAME}.zip" > "${NAME}.zip.sha256")
fi

echo "==> 交付目录：$OUT"
echo "==> 交付压缩包：$ZIP"
du -sh "$OUT" "$ZIP" 2>/dev/null || true
