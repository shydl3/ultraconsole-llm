"""命令行入口：`python -m ultra_rules.cli analyze --input ... --config ... --output ...`"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from .pipeline import AnalyzeOptions, run_analyze


def _parse_profile_map(raw: str | None) -> dict[str, str]:
    if not raw:
        return {}
    out: dict[str, str] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue
        k, v = pair.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def cmd_analyze(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="ultra_rules.cli analyze", description="离线视频事件检测分析")
    p.add_argument("--input", required=True, help="视频文件或目录")
    p.add_argument("--config", required=True, help="规则配置 JSON")
    p.add_argument("--output", required=True, help="运行输出根目录（会在其下创建 run_id 子目录）")
    p.add_argument("--run-id", default=None, help="指定 run_id；默认按时间戳生成")
    p.add_argument("--cache-dir", default=None, help="预测缓存目录；未指定则不使用缓存")
    p.add_argument("--device", default=None, choices=["cpu", "cuda"], help="覆盖配置中的 device")
    p.add_argument("--profile", default=None, help="强制使用的 cameraProfileId")
    p.add_argument(
        "--profile-map",
        default=None,
        help="按文件名子串映射 profile，例：'20260608=c2,未拦截=d2,正常拦截=d2'",
    )
    p.add_argument("--no-evidence", action="store_true", help="不保存证据截图")
    p.add_argument("--no-cache-write", action="store_true", help="不写入预测缓存")
    p.add_argument("--no-cache-read", action="store_true", help="不读取已有缓存")
    args = p.parse_args(argv)

    opts = AnalyzeOptions(
        input_path=os.path.abspath(args.input),
        config_path=os.path.abspath(args.config),
        output_dir=os.path.abspath(args.output),
        run_id=args.run_id,
        cache_dir=os.path.abspath(args.cache_dir) if args.cache_dir else None,
        device=args.device,
        profile_id=args.profile,
        profile_map=_parse_profile_map(args.profile_map),
        save_evidence=not args.no_evidence,
        use_cache=not args.no_cache_read,
        write_cache=not args.no_cache_write,
    )
    summary = run_analyze(opts)
    print(json.dumps({"runId": summary.run_id, "totals": summary.totals}, ensure_ascii=False, indent=2))
    return 0


def cmd_list_runs(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="ultra_rules.cli list-runs")
    p.add_argument("--output", required=True)
    args = p.parse_args(argv)
    root = os.path.abspath(args.output)
    if not os.path.isdir(root):
        print("[]")
        return 0
    out: list[dict[str, Any]] = []
    for name in sorted(os.listdir(root), reverse=True):
        rj = os.path.join(root, name, "run.json")
        if not os.path.isfile(rj):
            continue
        try:
            with open(rj, "r", encoding="utf-8") as f:
                data = json.load(f)
            out.append(
                {
                    "runId": data.get("runId"),
                    "startedAt": data.get("startedAt"),
                    "finishedAt": data.get("finishedAt"),
                    "totals": data.get("totals", {}),
                    "videos": len(data.get("videos", [])),
                }
            )
        except Exception as e:  # noqa: BLE001
            print(f"warn: cannot parse {rj}: {e}", file=sys.stderr)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    if not argv:
        print(__doc__)
        return 2
    cmd, rest = argv[0], argv[1:]
    if cmd == "analyze":
        return cmd_analyze(rest)
    if cmd == "list-runs":
        return cmd_list_runs(rest)
    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
