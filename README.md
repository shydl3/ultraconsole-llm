# Ultralytics 工业级前端推理控制台

**中文** | [English](README_EN.md)

<p align="center">
  <img src="https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=white&style=flat-square" alt="React" />
  <img src="https://img.shields.io/badge/TypeScript-6.0-3178C6?logo=typescript&logoColor=white&style=flat-square" alt="TypeScript" />
  <img src="https://img.shields.io/badge/Vite-8.0-646CFF?logo=vite&logoColor=white&style=flat-square" alt="Vite" />
  <img src="https://img.shields.io/badge/Python-3.10-3776AB?logo=python&logoColor=white&style=flat-square" alt="Python" />
  <img src="https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi&logoColor=white&style=flat-square" alt="FastAPI" />
  <img src="https://img.shields.io/badge/Ultralytics-YOLOv8-FF6B00?logo=ultralytics&logoColor=white&style=flat-square" alt="YOLOv8" />
  <img src="https://img.shields.io/badge/OpenCV-4.x-5C3EE8?logo=opencv&logoColor=white&style=flat-square" alt="OpenCV" />
</p>

<p align="center">
  <a href="https://github.com/WangQvQ/ultraconsole/blob/main/LICENSE"><img src="https://img.shields.io/github/license/WangQvQ/ultraconsole?style=flat-square&color=blue" alt="License" /></a>
  <a href="https://github.com/WangQvQ/ultraconsole/stargazers"><img src="https://img.shields.io/github/stars/WangQvQ/ultraconsole?style=flat-square&color=yellow" alt="Stars" /></a>
  <a href="https://github.com/WangQvQ/ultraconsole/issues"><img src="https://img.shields.io/github/issues/WangQvQ/ultraconsole?style=flat-square&color=red" alt="Issues" /></a>
  <a href="https://github.com/WangQvQ/ultraconsole/commits"><img src="https://img.shields.io/github/last-commit/WangQvQ/ultraconsole?style=flat-square&color=green" alt="Last Commit" /></a>
  <img src="https://img.shields.io/badge/PRs-Welcome-brightgreen?style=flat-square" alt="PRs Welcome" />
</p>

<p align="center">
  <img src="https://img.shields.io/badge/YOLOv8-Detection-FF6B00?style=flat-square&logo=ultralytics&logoColor=white" alt="Detection" />
  <img src="https://img.shields.io/badge/Segmentation-Instance-8B5CF6?style=flat-square" alt="Segmentation" />
  <img src="https://img.shields.io/badge/Pose-COCO_17-EC4899?style=flat-square" alt="Pose" />
  <img src="https://img.shields.io/badge/ByteTrack-Tracking-10B981?style=flat-square" alt="ByteTrack" />
  <img src="https://img.shields.io/badge/GPU-CUDA-76B900?logo=nvidia&logoColor=white&style=flat-square" alt="CUDA" />
  <img src="https://img.shields.io/badge/WebSocket-Real--Time-FFA500?style=flat-square" alt="WebSocket" />
  <img src="https://img.shields.io/badge/Webhook-Alert-9333EA?style=flat-square" alt="Webhook" />
  <img src="https://img.shields.io/badge/i18n-中英双语-2563EB?style=flat-square" alt="i18n" />
  <img src="https://img.shields.io/badge/Heatmap-Spatial-EAB308?style=flat-square" alt="Heatmap" />
</p>

面向工业质检 / 安防监控 / 算法调优场景的 **通用目标检测可视化控制台**：模型热切换、参数热更新、本地视频分析输入、OSD 叠加（含分割/姿态）、ByteTrack 跟踪 + 越线/区域计数事件、空间热力图、GPU/CPU/延迟监控、Webhook 告警出系统、配置一键导入导出。

主页直接显示 **Video**。Image、RTSP、Webcam、Wall、离线 Rules 页面及其专属 API 已移除。`Rules/` 目录仅保留 Video 语义分析所需的 VLM、姿态观测和几何代码；现有语义分析数据路径保持兼容。

## 页面预览（Screenshot）

![Ultralytics 工业级前端推理控制台预览](preview.png)

## 项目亮点（为什么值得用）

- **工业暗黑风 + Neo-Skeuomorphism**：适合长时间盯盘，关键状态一眼可读。
- **零阻塞实时重绘**：OSD 叠加基于 Canvas，不把高频图形更新塞进 React render；ResizeObserver 只挂一次。
- **后台推理**：视频帧推理通过 `asyncio.to_thread` 执行，YOLO 调用串行化。
- **顺序视频分析**：Video 顺序处理设定的采样点，处理较慢时延长分析时间。
- **可观测闭环**：HUD 遥测 + 系统资源面板（CPU/GPU/显存/温度/功耗）+ 推理延迟 P50/P95/P99 + 结构化事件流 + CSV 导出。
- **告警出系统**：内置钉钉 / 企微 / 飞书 / Slack / 通用 JSON 五种 Webhook 格式，按 (kind, ref) 维度冷却去重。
- **目标跟踪 + 事件**：内置 ByteTrack，画"计数线"/"计数区"自动统计 in/out / enter/leave，trackId 稳定颜色 + 轨迹尾迹。
- **断线保留报告**：Video 断线会结束本次分析并保留部分报告。
- **配置可迁移**：一键导出 JSON（参数/引擎/OSD/ROI/计数线区/告警/Webhook 全套），到另一台机拖拽即生效。
- **可替换推理内核**：默认 `pip install ultralytics`，支持切换到你"魔改版 ultralytics"。
- **中英双语**：界面支持中文/英文一键切换（顶栏 `中/EN` 按钮），默认中文；README 提供中英双语版本。

## 功能一览

### 模型与环境控制

- **Model Hub**：从 `backend/models/` 下拉选择模型（`.pt/.onnx/.engine`），路径白名单校验
- **Engine**：CPU/CUDA 切换；同设备直接 no-op，不同设备只 warmup（不重 load 权重）
- **NMS**：Conf/IoU 滑块实时调整，前端 200ms 防抖回写
- **Class Filter**：多选标签过滤类别
- **Tracking**：Video 分析自动启用 ByteTrack，bbox 自动带 `#trackId`

### 输入源

- **Video**：本地视频顺序采样分析（可设 FPS），支持**拖拽**、独立 ByteTrack 跟踪、越线/区域计数、视频时间与分析 CSV 导出。

### 可视化与效率

- **OSD**：BBox / Labels / Masks（实例分割半透明）/ Keypoints（COCO-17 骨架）/ Trails（轨迹尾迹）
- **稳定颜色**：按 trackId 哈希到 HSL，跨帧颜色一致
- **Telemetry HUD**：FPS / 耗时（Pre/Infer/Post）
- **ROI**：矩形/多边形绘制 +"仅显示 ROI 内目标"（前端显示过滤），支持触屏；计数范围由 Counting Line / Zone 单独定义
- **Counting Line / Zone**：在画面上画线/画区，自动统计跨线/进出区

### 监控与数据沉淀

- **Event Logger**：滚动结构化日志，CSV 导出 `/api/logs/export.csv`
- **Alert Engine**：类别连续 N 帧触发告警（红色呼吸边框 + 可选蜂鸣）；Video 录像分析不触发此告警
- **Counters / Events**：实时显示线 in/out、区域 当前/累计 数量 + 最近 30 条事件流
- **System Stats**：CPU% / Mem / 多 GPU util/显存/温度/功耗 + 推理延迟 P50/P95/P99 + 60 帧 sparkline
- **Webhook**：钉钉 / 企微 / 飞书 / Slack / 通用 JSON 五种格式；告警 + 计数事件实时推送；按 (kind, ref) 冷却去重，Video 录像事件不派发
- **Config Import / Export**：一键导出全套 JSON 配置；拖拽 JSON 到面板即应用

## Video 录像分析与计数

1. 在 Model Hub 选择模型，切换到 **Video**，选择或拖入本地视频。浏览器解码失败时，自动上传并通过后端 PyAV / FFmpeg 调用 NVIDIA NVDEC（CUVID）硬件解码和 NVENC 硬件编码，生成 H.264 MP4 兼容预览（仅视频），显示转换进度并支持取消；原始文件不变。默认上传上限为 2 GiB（`ULTRA_VIDEO_MAX_UPLOAD_BYTES`），长录像需等待转换完成后再分析。转换成功的 MP4 统一永久保存在项目 `data/video-previews/`（可用 `ULTRA_VIDEO_PREVIEW_DIR` 指定其他绝对路径）；文件名为原视频内容的 SHA-256，同名 JSON 记录原文件名。再次上传相同内容时直接复用，不重新转码，改名和重启后端也不影响。切换视频或离开页面只释放会话；24 小时清理仅针对会话和临时上传，不删除已转换视频。可直接打开目录中的 MP4，也可手动删除不再需要的 MP4 与同名 JSON 释放空间。
2. 点击「画计数线」，在画面中点击两个端点；或点击「画计数区」，依次点击至少三个顶点，再点击起点闭合。画好后点击「完成绘制」。
3. 设置采样 FPS（1–30，默认 8），点击「从头分析（含跟踪）」。Video 自动启用 ByteTrack，无需勾选实时流的 Tracking 开关。
4. 在 Counters / Events 查看越线方向、区域当前数量、累计进入次数和事件的视频时间。
5. 完成或停止后，点击「导出分析 CSV」。切换文件或离开 Video 前请先导出，报告仅保存在当前页面内存中。

预览转码固定使用 NVIDIA GPU 0，不受 YOLO 推理设备选择影响。GPU、驱动或视频编码规格不受支持时，任务会失败并显示原因，**不会回退到 CPU 编解码**。转码在独立进程中执行，取消任务会终止该进程并释放资源。文件读写、封装、帧内存搬运以及必要的像素格式转换仍有 CPU 工作，因此 CPU 占用不为零不代表使用了软件编解码。此约束针对上传后的服务端预览转码，浏览器播放与其他分析路径独立。

每次开始都会清空本次统计、从视频起点重新分析，并建立新的跟踪连接。分析期间关闭播放控制；停止后可以预览或拖动进度，再次开始仍会从头统计。修改模型、设备、推理参数或计数规则会停止当前分析，避免混用结果。

分析按 `0、1/FPS、2/FPS…` 的视频时间顺序采样，收到上一帧结果后才发送下一帧。处理速度取决于推理性能；这不是逐原始帧分析，低采样率、检测漏检和跟踪 ID 变化仍可能影响计数。

| 指标 / 报告内容 | 含义 |
|---|---|
| 线 `in / out` | 跟踪框中心穿过所画有限线段的两个方向；方向取决于端点顺序 |
| 区域 `current` | 当前采样帧中位于区域内的跟踪目标数量 |
| 区域 `total` | 累计进入次数，包含首次检测时已在区域内的目标，不是去重人数 |
| 事件时间 | 视频内的时间，例如 `00:02:35.125` |
| CSV 元信息 | 文件名、视频时长、采样 FPS、已处理帧数、最后采样时间、模型、参数和规则快照 |
| CSV 明细 | 计数汇总和全部事件，不受界面保留最近 500 条、显示最近 30 条的限制 |
| CSV 状态 | `completed` / `stopped` / `error`；停止或失败后的结果只覆盖已处理部分 |

录像分析不触发实时 Alert Engine 或 Webhook，避免将历史事件作为当前告警发送。分析 CSV 与 Event Logger 的运行日志 CSV 是两种不同报告。

## 目录结构

```
backend/                    # FastAPI + Ultralytics 推理服务
├── app/
│   ├── main.py             # REST + Video WS（/ws/infer）
│   ├── config.py          # 环境变量与资源限制
│   ├── security.py        # REST / WS 鉴权与 URL 校验
│   ├── infer_runtime.py    # YOLO 加载/推理/跟踪，masks + keypoints 提取
│   ├── system_stats.py     # CPU/GPU 监控 + 推理延迟 ring buffer
│   ├── webhook.py          # Webhook 配置持久化 + 多格式派发
│   ├── schemas.py          # Pydantic 模型（含 trackId/masks/keypoints/SystemStats）
│   ├── logging_store.py    # 内存 ring buffer + CSV 导出
│   └── state.py            # 全局可变状态（params / engine / logs）
├── models/                 # 权重文件（已 gitignore）
├── tests/                  # 后端回归测试
├── requirements-dev.txt    # pytest / Ruff
└── requirements.txt

frontend/                   # React 19 + Vite + TypeScript + zustand
└── src/
    ├── pages/ConsolePage   # 三栏布局（左控制 / 中视图 / 右监控），可拖拽分隔条
    ├── ui/
    │   ├── control/        # ControlPanel: Model/Engine/NMS/Class/Tracking
    │   ├── viewer/
    │   │   ├── tabs/       # Video
    │   │   └── widgets/    # CanvasOverlay / VideoCanvasOverlay / RoiOverlay /
    │   │                   # EventEditOverlay / TelemetryHUD
    │   ├── monitor/        # SystemStatsCard / WebhookCard / ConfigCard /
    │   │                   # MonitorPanel（Counters/Events/Alert/Logs）
    │   ├── status/         # 顶栏 SystemStatusBar
    │   └── primitives/     # Card / NeoButton
    ├── store/              # zustand 全局 store
    ├── api/                # REST / WS 工具 + contracts.ts 共享类型
    ├── i18n/               # 国际化（中文/英文）
    └── utils/              # draw / events / roi / configIO / reconnectWs /
                            # useEventTracker / useSystemStats /
                            # useWebhookDispatcher / badcase / videoAnalysis / binaryFrame
```

## 快速开始

环境要求：Python 3.10+、Node.js 22.12+、npm。CUDA 推理需要兼容的 NVIDIA 驱动与 PyTorch；仅使用 CPU 时不需要 NVIDIA 环境。

### 1）准备模型文件

把权重文件放到 `backend/models/*.pt`（或 `.engine/.onnx`）

### 2）启动后端（FastAPI）

#### 方式 A：venv

```bash
cd backend
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
uvicorn app.main:app --reload --port 8001
```

#### 方式 B：conda

```bash
conda create -n ultraconsole python=3.10 pip -y --override-channels -c conda-forge
conda activate ultraconsole
cd backend
python -m pip install -r requirements.txt
uvicorn app.main:app --reload --port 8001
```

> 请完整安装 `requirements.txt`。无 NVIDIA 驱动时 GPU 指标不可用，CPU 推理仍可运行；`httpx` 是后端启动依赖，不能省略。

### 3）启动前端（React）

另开终端，从仓库根目录执行：

```bash
cd frontend
npm ci
npm run dev
```

默认打开 `http://localhost:5173`，选择模型后开始使用。前端开发服务器把 `/api` 与 `/ws` 代理到 `http://127.0.0.1:8001`。

## 常用配置

| 项 | 默认 | 来源 |
|---|---|---|
| 后端端口 | `8001` | 启动命令 |
| 模型目录 | `backend/models/`（由后端代码位置解析） | 环境变量 `ULTRA_MODELS_DIR` |
| Webhook 持久化 | `backend/.webhook.json`（gitignore） | 环境变量 `ULTRA_WEBHOOK_FILE` |
| 默认设备 | `cpu` | 环境变量 `ULTRA_DEVICE=cuda` |

### 安全与资源限制

配置通过后端进程环境变量读取。若使用文件，在 `backend/` 下复制 `cp .env.example .env`，修改后通过 `uvicorn app.main:app --reload --port 8001 --env-file .env` 显式加载；仅复制文件不会自动生效。

- `ULTRA_API_TOKEN`：设置后保护除健康检查外的 REST API 和全部 WebSocket；前端构建时设置同值的 `VITE_ULTRA_API_TOKEN`
- `ULTRA_CORS_ORIGINS`：允许的前端 Origin，逗号分隔；默认仅本机开发地址
- `ULTRA_MAX_UPLOAD_BYTES` / `ULTRA_MAX_IMAGE_PIXELS`：上传文件与解码后像素上限
- `ULTRA_MAX_WS_BASE64_CHARS`：WebSocket 帧编码长度上限，默认 16 MiB 字符
- `ULTRA_MAX_TRACKER_CONTEXTS`：独立跟踪上下文上限，默认 10；每个上下文会增加模型内存/显存占用
- `ULTRA_ALLOW_PRIVATE_WEBHOOKS`：默认 `false`，防止 Webhook 访问本机、内网和链路本地地址

启用鉴权时，在 `frontend/.env.local` 设置相同的 `VITE_ULTRA_API_TOKEN`，然后重启开发服务器或重新构建。未设置后端 Token 时不校验凭证。REST 客户端可使用 `Authorization: Bearer <token>`；浏览器 WebSocket 通过 `ultra-token.<base64url-token>` 子协议携带凭证。

API Token 会被编译进浏览器端资源，因此它是部署级共享访问凭证，不应复用于其他系统。

## REST / WebSocket API

### REST

| Method | Path | 说明 |
|---|---|---|
| `GET` | `/api/health` | 健康检查 |
| `GET` | `/api/models` | 列出 `models/` 下可用模型 + 当前已加载模型回填 |
| `POST` | `/api/models/select` | 加载模型（`{modelId}`，做路径白名单 + 后缀校验） |
| `POST` | `/api/engine/select` | 切换 CPU/CUDA（`{device}`，同设备 no-op） |
| `GET` `/POST` | `/api/params` | 获取/更新 `{conf, iou, classFilter, track}` |
| `GET` | `/api/system/stats` | CPU/GPU/Mem + 推理延迟分位 |
| `GET` `/POST` | `/api/webhook` | 取/存 webhook 配置 |
| `POST` | `/api/webhook/test` | 联通性测试 |
| `POST` | `/api/notify` | 派发告警/事件到 webhook |
| `GET` | `/api/logs/export.csv` | 滚动日志 CSV 导出 |

### WebSocket

- `/ws/infer` — Video 推帧通道：客户端发 `{type:'frame', imageJpegBase64, ...}`，服务端回 `{type:'pred'|'log'}`；接受 `{type:'ping'}` → `{type:'pong'}`

## 使用"魔改版 ultralytics"的方式

### 方式 1：本地源码可编辑安装

```bash
conda activate ultraconsole   # 或 source backend/.venv/bin/activate
python -m pip uninstall -y ultralytics
python -m pip install -e ~/work/ultralytics

cd backend
uvicorn app.main:app --reload --port 8001
```

修改源码后无需重新安装，重启 uvicorn 即可生效。

### 方式 2：把依赖指向本地路径

把 `backend/requirements.txt` 里的 `ultralytics` 改成：

```txt
-e /absolute/path/to/ultralytics
```

## Webhook 配置（钉钉示例）

1. 在监控面板 Webhook 卡片：
   - 勾选"启用"
   - URL 粘贴钉钉自定义机器人 webhook（推荐用"自定义关键词"安全模式）
   - 格式选"钉钉"，最低等级 `WARN`，冷却 30 秒
   - 事件类型勾上 `alert`、`line.cross`、`zone.enter`
   - 点"发送测试" → 群里收到 `ℹ️ [INFO] Webhook 测试`
Video 录像分析的计数事件仅进入报告，不派发实时 Webhook。可通过“发送测试”检查通知连通性。

## 架构图

```mermaid
flowchart TB
  UI["Web Console: Control + Video + Monitor"]
  Media["Local video files"] --> UI
  UI -->|REST| API["FastAPI: models, parameters, stats, library, webhook"]
  UI -->|Video frames: /ws/infer| Runtime["Ultralytics inference + isolated tracking"]
  API --> Runtime
  UI -->|POI analysis| VLM["Background pose + Qwen semantic analysis"]
  VLM --> Storage["Results, evidence, source videos and caches"]
  API --> Storage
  API -->|Notifications| Webhook["Webhook channels"]
  Runtime -->|Predictions + telemetry| UI
```

## FAQ

### 1）模型/权重文件会不会被提交到开源仓库？

不会：仓库已在 `.gitignore` 忽略 `backend/models/**/*.pt|onnx|engine` 等大文件类型，以及 `backend/.webhook.json`（含外部 URL 不入仓库）。

### 2）GPU 监控面板显示"NVML 不可用"？

说明 `nvidia-ml-py` 未安装或当前机器无 NVIDIA 驱动。CPU/Mem 仍可显示（依赖 `psutil`）；不影响推理。

### 3）Webhook 派发被钉钉拒绝？

钉钉自定义机器人若开了"加签安全"或"IP 白名单"会失败。建议先用"自定义关键词"模式（最简单），把关键词设成"告警"/"UltraConsole" 等。

### 4）Video 分析断线后如何处理？

断线会结束当前分析，可导出部分报告。重新开始会建立新的跟踪上下文，并从视频起点分析。

### 5）配置导出的 JSON 里有 webhook URL 明文吗？

有。导出文件含敏感 URL，UI 上没遮挡。如需更严格可在导出前手工删除 webhook 字段，或后续加"导出时屏蔽 webhook"开关。

## 开发与验证

以下命令从仓库根目录执行，并使用已激活的后端虚拟环境：

```bash
python -m pip install -r backend/requirements-dev.txt
python -m pytest
ruff check backend/app backend/tests
npm --prefix frontend ci
npm --prefix frontend test
npm --prefix frontend run lint
npm --prefix frontend run build
```

GitHub Actions 在 push 和 pull request 时运行上述前后端测试、静态检查及前端构建。测试覆盖参数校验、访问控制、跟踪隔离、最新帧队列、二进制帧、Video 计数和报告等；模型精度、真实视频解码与 GPU/摄像头兼容性仍需使用实际设备和样本验证。

## 贡献指南

- 欢迎提 Issue/PR：
  - Bug：请带复现步骤、日志与视频格式与分析参数
  - Feature：建议先开 Issue 讨论接口与交互
- 路线图（已落地以 ✅ 标）：
  - ✅ 多路监控墙、ByteTrack + 事件、分割/姿态、GPU/CPU HUD、延迟分位、Webhook、WS 重连、拖拽上传、配置导入导出
  - ✅ 中英双语 i18n、空间热力图、Video 顺序分析与计数报告、可选 Token 鉴权、安全与资源限制、前后端测试与 CI
  - ⏳ 模型 A/B 对比、SQLite 持久化、截图导出、插件系统

### 记录与视频管理

点击控制台顶部 **记录与视频管理 ↗**，在独立窗口打开管理页，也可直接访问 `/library`（当前服务为 `http://localhost:5100/library`）。统一列出已保存的语义分析和转换视频，支持分类、名称／原文件名／ID 搜索、分页、视频预览与下载、分析结果和证据查看、重命名及勾选批量删除。

重命名修改持久保存的显示名称，保留原文件名、运行 ID 和视频缓存标识。删除需要在页面确认；运行中的分析不能删除。删除分析会清理该次运行的结果和证据，保留共享上传原视频；删除转换视频会清理 MP4、索引及旧预览硬链接，再次上传原片时重新转换。管理页覆盖磁盘上保存的记录，不包含当前会话的实时日志。

### 自定义语义事件规则

在 **Video → POI 视频语义分析 → 事件规则定义** 中逐条新增、编辑、复制规则。选择框用于批量操作，“参与分析”开关决定该规则是否用于分析；编辑和启停先作为草稿，点击单条保存、保存所选或保存全部修改后持久生效。批量添加支持每行一条描述，JSON/TXT 导入会先预览，可选择相同 ID 更新或全部另存为新规则。JSON 导出包含完整高级定义。

每条规则可配置跨线／进入区域／离开区域／起始在区域内、事件前后采样时间、判断条件及无法判断的情况。当前视频的 POI 绑定单独配置，不固化到通用规则库；失效的绑定会阻止启动。一次最多启用 30 条，库中最多保存 1000 条。存在草稿时，启动按钮显示“保存规则并开始分析”。

规则保存在 `Rules/data/vlm/event-rules.json`（跟随 `VLM_DATA_ROOT`）。多窗口修改采用版本检查，冲突时保留当前草稿，可先导出再重新加载。批量保存和删除全部校验后原子写入。分析启动时保存规则名称、版本和实际定义快照；后续修改或删除规则不影响正在运行的任务和历史分析。任务记录及独立管理窗口的详情中均可查看当时使用的规则。

### 视频内容描述与判断依据

新任务会分别保存并展示 Qwen 的“视频内容描述”和“判断依据”：描述目标动作的先后过程、事件相关现象及看不清的部分；依据对应事件条件，说明可见证据、时间窗口和缺失信息。控制台结果、分段证据和记录管理详情均可查看完整文本。详细描述仍是模型输出，便于人工复核，不代表已验证的事实。

默认最大输出为 1024 tokens，上限可设为 2048。浏览器已有的较低设置会保留，页面提供“使用推荐输出上限”按钮。旧记录保留原文，需重新运行才能获得新的视频描述。
