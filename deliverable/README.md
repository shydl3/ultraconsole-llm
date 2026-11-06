# UltraConsole 交付物（Docker / Windows 一键运行）

本目录把项目打包成**一个可在 Windows 11 上双击运行的交付物**。开发仍然在 WSL 完成，发版只需要重新构建镜像。

## 设计原则

| 决策 | 原因 |
|---|---|
| 镜像**不含** PyTorch/CUDA 依赖 | 体积大、与驱动强相关，首次启动装到卷 `ultra-envs` 里 |
| 镜像**不含**模型权重 | Qwen 2B 约 4GB，权重放 `models/` 卷，可单独替换/拷贝 |
| 前端构建进镜像，后端单端口 8001 托管 | 目标机器不需要 Node，也没有跨域/代理问题 |
| 运行数据全部在 `data/` | 升级只换镜像，记录、视频、缓存不丢 |
| Windows 侧只有 .bat/.ps1 启动器 | 交付对象没有 Linux 经验，全部 GUI 操作 |

## 目录结构

```
deliverable/
├── Dockerfile                  # 前端构建 + Python 运行时（不含依赖/权重）
├── VERSION                     # 交付版本号
├── compose/docker-compose.yml  # setup + app 两个服务，GPU 预留、健康检查
├── docker/
│   ├── entrypoint.sh           # setup / serve 入口，依赖未装时给出明确提示
│   ├── setup.sh                # 建 venv、装 torch/依赖、下载权重、校验模型
│   ├── download_models.py      # Qwen（HF）+ YOLO（GitHub）幂等下载
│   └── server.py               # 挂载 frontend/dist 到 /，与 API 同源
├── requirements/vlm.txt        # Qwen 环境依赖
├── package/                    # Windows 交付模板（bat + ps1 + 说明）
├── docs/                       # 部署 / 运维 / 验收文档（发给使用方）
├── test/
│   ├── check.sh                # 静态检查（无需 docker）
│   └── gpu-smoke.sh            # 有 GPU 的机器上端到端冒烟
├── build.sh                    # 构建镜像 + 导出 tar.gz + 打包
└── make-package.sh             # 组装 Windows 目录与 zip
```

## 构建

在 WSL（或任意带 Docker 的 Linux）里：

```bash
./deliverable/build.sh                 # 构建 + 导出 + 生成 Windows 包
./deliverable/build.sh --no-save       # 只构建镜像
TAG=1.0.1 ./deliverable/build.sh       # 指定版本
```

产物：

```
deliverable/dist/
├── images/ultraconsole-1.0.0.tar.gz
├── UltraConsole-1.0.0-Win64/          # 直接拷给使用方
└── UltraConsole-1.0.0-Win64.zip
```

> 首次构建会拉取 `node:22-bookworm-slim` 与 `python:3.10-slim-bookworm`。镜像本身约 600MB–1GB（不含依赖与权重）。

## 验证

```bash
# 静态检查：脚本语法、compose 结构、COPY 路径、BAT→PS1 引用、无写死路径
./deliverable/test/check.sh

# 有 GPU 的机器上（Linux/Windows Docker 均可）
PKG_DIR=./deliverable/dist/UltraConsole-1.0.0-Win64 ./deliverable/test/gpu-smoke.sh
```

`gpu-smoke.sh` 会验证：容器健康检查、首页 HTML、`torch.cuda.is_available()`、`h264_nvenc`/`h264_cuvid`、Qwen 环境、`/api/state`。

## 首次运行的下载量

| 内容 | 位置 | 约 |
|---|---|---|
| Python 依赖（两个 venv，含 torch cu124） | 卷 `ultra-envs` | 6–8 GB |
| Qwen3-VL-2B-Instruct | `models/Qwen3-VL-2B-Instruct` | 4.0 GB |
| yolo11m-pose / yolov8n | `models/detect` | 50 MB |

依赖安装完成后，requirements 不变时再次启动会直接跳过。

## 更新流程

```bash
TAG=1.0.1 ./deliverable/build.sh
```

把新的 `ultraconsole-1.0.1.tar.gz` 放进使用方的 `images/`，双击「更新.bat」。`ultra-envs` 卷在依赖未变化时复用，`data/`、`models/` 不变。

## 需要提前和对方 IT 确认

1. Docker Desktop 的**商业授权**（大企业需订阅）。
2. 是否允许开启虚拟化/WSL 2、安装显卡驱动。
3. 磁盘 ≥ 30GB、内存建议 ≥ 16GB。
4. `/data` 与 `/models` 通过 Windows 目录挂载，视频读写走 NTFS；2 小时以上大文件建议放本机 SSD。
5. `h264_nvenc` 必须实测：预览转码只走 NVDEC/NVENC，没有软件回退。

## 与开发环境的关系

- 代码仍然只有一份，路径默认自包含在项目内（`models/`、`backend/models`、`Rules/data/vlm`）。
- 交付镜像里由环境变量把这些路径改到挂载卷：`VLM_MODEL_PATH=/models/...`、`ULTRA_MODELS_DIR=/app/backend/models`、`VLM_DATA_ROOT=/data/vlm`。
- 开发机上的 `backend/.env`（`VLM_PYTHON`）不会进镜像；容器里由 compose 显式注入。
