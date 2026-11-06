#!/usr/bin/env bash
#
# 静态检查：脚本语法、compose 结构、Dockerfile 源路径、Windows 脚本引用。
# 不需要 docker / GPU，可在任意 Linux 上运行。
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
DELIVERABLE="$ROOT/deliverable"
FAIL=0

fail() { echo "  [失败] $*" >&2; FAIL=1; }
ok() { echo "  [通过] $*"; }

echo "==> Shell 语法"
while IFS= read -r script; do
  if bash -n "$script"; then ok "${script#$ROOT/}"; else fail "${script#$ROOT/}"; fi
done < <(find "$DELIVERABLE" -name '*.sh' -type f | sort)

echo "==> Compose YAML"
python3 - "$DELIVERABLE/compose/docker-compose.yml" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
try:
    import yaml
except ImportError:
    yaml = None

if yaml is None:
    print("  [跳过] 未安装 PyYAML，无法解析 YAML")
    raise SystemExit(0)

data = yaml.safe_load(path.read_text())
assert data["services"]["app"]["ports"], "app 缺少端口映射"
assert data["services"]["app"]["deploy"]["resources"]["reservations"]["devices"], "app 缺少 GPU 预留"
assert data["services"]["app"]["depends_on"]["setup"]["condition"] == "service_completed_successfully", "app 应等待 setup 成功"
assert "ultra-envs" in data["volumes"], "缺少 ultra-envs 卷"
print("  [通过] compose 结构")
PY

echo "==> Dockerfile COPY 源路径"
python3 - "$ROOT" "$DELIVERABLE/Dockerfile" <<'PY'
import sys
from pathlib import Path

root = Path(sys.argv[1])
dockerfile = Path(sys.argv[2])
missing = []
for line in dockerfile.read_text().splitlines():
    line = line.strip()
    if not line.upper().startswith("COPY ") or "--from=" in line:
        continue
    parts = line.split()[1:]
    if len(parts) < 2:
        continue
    for source in parts[:-1]:
        if "*" in source or "?" in source:
            continue
        if not (root / source).exists():
            missing.append(source)
if missing:
    print("  [失败] 缺少源路径：" + ", ".join(sorted(set(missing))))
    raise SystemExit(1)
print("  [通过] Dockerfile 源路径")
PY

echo "==> Windows 启动脚本引用"
python3 - "$DELIVERABLE/package" <<'PY'
import sys
from pathlib import Path

package = Path(sys.argv[1])
missing = []
for bat in package.glob("*.bat"):
    text = bat.read_text(encoding="utf-8", errors="replace")
    for token in text.split('"'):
        if not token.endswith(".ps1"):
            continue
        relative = token.replace("%~dp0", "").replace("\\", "/")
        candidate = package / relative
        if not candidate.is_file():
            missing.append(f"{bat.name} -> {token}")
if missing:
    print("  [失败] " + "; ".join(missing))
    raise SystemExit(1)
print("  [通过] BAT 引用的 PS1 均存在")
PY

echo "==> 禁止写死开发机路径"
if grep -RIn --exclude-dir=dist --exclude='*.md' --exclude=check.sh -E '/home/[a-z]+/|/root/' "$DELIVERABLE" >/dev/null 2>&1; then
  grep -RIn --exclude-dir=dist --exclude='*.md' --exclude=check.sh -E '/home/[a-z]+/|/root/' "$DELIVERABLE" >&2
  fail "交付物中仍有写死的家目录路径"
else
  ok "未发现 /home 或 /root 绝对路径"
fi

echo "==> 模板占位符"
if grep -q '__TAG__' "$DELIVERABLE/package/env.example"; then ok "env.example 含 __TAG__ 占位符"; else fail "env.example 缺少 __TAG__"; fi

echo
if [[ "$FAIL" == "0" ]]; then echo "静态检查全部通过。"; else echo "静态检查存在失败项。" >&2; exit 1; fi
