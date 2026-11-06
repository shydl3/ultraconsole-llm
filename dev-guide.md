# UltraConsole VLM 语义分析 — 开发指南（Dev Guide）

> 本文档面向两类读者：
> - **非技术读者（业务 / 管理 / 其他部门）**：请先读「第 1 节 执行摘要」和「第 2 节 项目目标」，了解我们在做什么、做到哪了、下一步为什么。
> - **开发者 / 后续 AI 协作**：从「第 3 节」开始是技术细节、代码地图、路线图与验收标准，可直接据此继续开发。

---

## 1. 执行摘要（给非技术读者）

本项目是一个**对视频做“事件语义分析”的工具**：用户上传一段监控视频，在画面上框出“计数线 / 计数区 / 信号区”，定义一条“事件规则”，系统自动：

1. 用视觉模型（YOLO 姿态）检测并跟踪画面里的人；
2. 根据“谁在何时进入/离开区域、是否跨线”生成**候选事件时间点**；
3. 用视觉语言大模型（Qwen3-VL）判断每个候选点“事件是否真的发生”；
4. 输出带时间点、原因和证据截图的结果，供人工复核。

**当前状态一句话**：候选事件的时间点已经可靠；**唯一的主要短板是“单个事件判断”的准确率还不够高**（存在较多误报 / 漏报），而我们的目标是把准确率提升到**可上线生产环境部署**的水平。

**我们已经做了两件关键事，直接针对误报：**

- **把“门框红灯是否亮起”这类可明确测量的信号，改成用传统图像算法（CV）直接检测**，不再让大模型“看”一个在缩略图里只有一两个像素的红灯——这消除了最大的一类误报（模型把规则文字复述成“红灯亮起”）。
- **把判定改成“确定性优先 + 大模型兜底”**：能用轨迹和红灯状态算清楚的，直接算；算不清楚的才交给大模型复核。

**本轮又做完两件事：**

- **把大模型回答改成“逐条件、带证据时间”的结构化输出**：模型对每个判定条件给“是 / 否 / 不确定 + 证据画面秒数”，由代码按“所有条件都满足才算发生”的合取逻辑**计算**最终结论，不再直接采信模型的一段话；提示词同时改为**“默认否认”**（看不清就不算发生）并**删掉衣着外貌等无关描述**。GPU 实测：描述更短更聚焦、逐条件证据可点击回放。
- **控制台启动即就绪**：后端启动自动选 GPU（CUDA）并加载姿态模型 `yolo11m-pose.pt`，前端无需再手动选设备 / 模型（下拉与按钮仍保留可手动切换）。

**接下来要做的（按优先级）：**

- **P1（剩余）**：让大模型“看得更清楚”（高分辨率裁剪、围绕红灯亮起时刻抽帧）、并评估更大更强的模型（4B/8B、self-consistency）。
- **P2**：建立**人工标注的评测集和量化指标**（准确率、召回率、误报率），用数据证明“是否达到上线标准”，并接入人工复核闭环。

详细方案见第 6 节。这份文档的目标是：让任何一位开发者（或 AI）接手后，不需要重新摸索，就能按路线图继续推进。

---

## 2. 项目定位与目标

- **定位**：UltraConsole 的二次开发版本。系统由两个子系统组成：
  1. **经典 CV 实时视频控制台**（保留自上游）：实时 YOLO 检测、计数线/区、系统监控、告警引擎、事件日志等（见 `ControlPanel` / `MonitorPanel` / `VideoTab` 的基础分析 / `ws/infer`）。
  2. **VLM 语义事件分析**（本项目核心扩展）：上传视频 → 画 POI/信号区 → 事件规则 → 候选事件 → 大模型判定。
- **已移除**：仅 **Webhook 推送**能力（前端 `WebhookCard`/`useWebhookDispatcher`、后端 `webhook.py` 与 `/api/webhook*`、`/api/notify`）。记录/视频管理已改为**单页内切换**（不再弹窗）。
- **目标**：把 VLM 的“事件级判定”准确率提升到**生产环境可部署**水平，即误报、漏报低到业务可接受，且结果可审计、可回放。

> 上游说明：本项目是作者开源项目的衍生作品，保留 LICENSE 与署名；开发在 `llm` 分支，`origin` 指向自有仓库，`upstream` 指向原作者仓库。

---

## 3. 系统如何工作（架构概览）

### 3.1 一次分析任务的完整链路

```
前端(上传视频 + 画线/区/信号区 + 定义规则)
        │  POST /api/vlm/runs/from-rules
        ▼
后端 event_rules.resolve()  →  RunRequest(request.json)
        │
        ▼
runner.py 依次执行三个子进程阶段：
  ① tracking    姿态跟踪：YOLO-Pose + ByteTrack → observations.sqlite
  ② cues        信号检测：CV 红灯检测 → cues.json（可选，仅当规则绑定了信号区）
  ③ understanding 判定：确定性判定 或 VLM 复核 → results.jsonl
        │
        ▼
前端轮询 /runs 与 /results，展示结果 + 证据截图
```

### 3.2 候选事件如何生成（时间点）

- 候选生成在 `Rules/src/ultra_rules/vlm/poi.py` 的 `PoiTracker`：对每个 `(跟踪ID, 跟踪段, POI)` 维护状态机，在跨线 / 进区 / 出区 / 首次在区内时产生**触发（trigger）**。
- `Rules/src/ultra_rules/vlm/observations.py` 再按“启用的规则 + 触发类型 + 绑定的 POI”过滤，写入 `candidates.jsonl`。
- **这部分（候选时间点）用户已确认可靠，不要再改动其生成逻辑。**

### 3.3 判定如何产生（结果）

- `Rules/src/ultra_rules/vlm/qwen_worker.py` 的 `understand()` 对每条候选：
  - 若规则绑定了**信号区（cueIds）** → 先走 `decision.py` 的**确定性判定**；
  - 确定性给出 `match` / `no_match` → 直接出结果，不调用大模型；
  - 确定性无法判定（`uncertain`）→ 交给 VLM 复核（结构化逐条件输出 + 默认否认提示词，见 6.2）。
- `Rules/src/ultra_rules/vlm/results.py` 负责解析模型回答与聚合（当前版本 `model-decision-v5`）：解析模型逐条件输出 `conditions`（`index / status(yes|no|unknown) / evidenceTimeSec`），由 `decision_from_conditions` 按**合取真值表**计算 decision（任一 `no`→`no_match`；全 `yes`→`match`；否则 `uncertain`），模型自由文本结论降级为 `modelDecision` 仅供对照；证据时间做校验（越窗 / 约等于 `triggerSec` / 缺失 → 该条件降级 `unknown` 并记警告）；`conditions` 缺失或数量不符则回退采信 `modelDecision`（保持 v4 行为）。质量项（可见度 / 帧数 / 目标过小）仅作 advisory 警告，不否决。

---

## 4. 当前开发进度（已完成）

| 模块 | 内容 | 状态 |
|---|---|---|
| VLM 语义分析管线 | 上传视频 → 画 POI → 规则 → 候选 → 判定 → 结果 | ✅ 已完成 |
| P0 确定性判定 | 红灯 CV 检测 + 入口线退回判定 + 后处理 veto + 触发收紧 | ✅ 已完成 |
| P0 边界：红灯阈值校准 | `cli calibrate` 测量工具 + 前端“信号区阈值”面板 | ✅ 已完成 |
| P0 边界：入口方向 | 计数线 `entryDirection`，翻转只作用于最新一条线 | ✅ 已完成 |
| 收敛调整 | 仅移除 Webhook；保留实时控制台与基础分析；记录管理改为单页内切换 | ✅ 已完成 |
| Schema 迁移容错 | `event-rules.json` 旧记录缺新字段时用 `.get` 读取 | ✅ 已完成 |
| P1② 结构化条件输出 | 模型逐条件 `status + evidenceTimeSec`，`results.py` 合取真值表算 decision（`model-decision-v5`）；前端逐条件证据展示 | ✅ 已完成 |
| P1③ 默认否认提示词 | 去衣着/外貌、默认否认、3 个 few-shot、输出含 conditions（`target-event-described-json-v6`） | ✅ 已完成 |
| 控制台启动自选设备/模型 | 后端 lifespan 自动选 CUDA（不可用回退 CPU）+ 加载 `yolo11m-pose.pt`；`GET /api/state` 回显；前端下拉/按钮仍可手动切换 | ✅ 已完成 |
| 库记录命名 + 处理时长 | 分析记录展示名 = 原视频名 + 分钟级时间戳（`library.json` displayName）；计时从「开始分析」起算，库列表/详情显示时长 | ✅ 已完成 |
| P1（其余：证据 / 模型） | 高分辨率 ROI 抽帧、4B/8B、self-consistency、置信度校准 | ⬜ 待做 |
| P2（评测 / 生产化） | 标注集、指标、人工复核、监控 | ⬜ 待做 |

### 4.1 P0 已落地的关键决策（请勿推翻，除非业务变化）

1. **报警信号 = 安检门门框的竖直红色 LED 灯带**；顶部绿色指示灯按规则忽略。
2. **红灯亮灭用经典 CV（红色像素占比 + 持续时间）检测**，不用大模型判断。
3. **判定策略 = 确定性优先 + VLM 仅复核不确定样本**。
4. **“退回” = 红线亮起后 5 秒内，反向跨回入口计数线**；触发只保留进入类（`line.cross`）。
5. **红灯阈值在前端“信号区阈值”高级设置里可编辑**，随任务快照冻结，每相机各存一套；校准工具输出建议值。
6. **入口方向翻转只作用于最新画的一条计数线**。
7. **仅移除 Webhook**；实时控制台（经典 CV 检测/计数/监控）与基础分析保留。
8. **启动即就绪，无需手动选设备/模型**：后端启动时（FastAPI `lifespan` → `_autostart`）自动选 CUDA（不可用回退 CPU）并加载 `yolo11m-pose.pt`；前端 `ControlPanel` 挂载读 `GET /api/state` 回显当前引擎与模型，Model Hub 下拉与 CPU/CUDA 按钮仍可手动切换。VLM 分析侧 `poseDevice` 默认 `cuda`、姿态模型固定 `yolo11m-pose.pt`。可用 `ULTRA_AUTOSTART / ULTRA_DEFAULT_MODEL / ULTRA_DEVICE` 配置；自动选择失败只记日志、降级为 CPU/无模型，不阻塞启动。
9. **库记录展示名与处理时长语义**：VLM 记录展示名 = `原视频名_YYYYMMDD_HHMM`（分钟级，如 `监控.mp4_20261105_1730`），由 `prepare()` 写 `library.json` 的 `displayName`，内部 `run_id` 不再作展示主标识；处理时长 `startedAt` 在 `prepare()`（用户点「开始分析」那一刻）写入 `status.json`，`runner` 不得重置，`durationSec = finishedAt(或当前时间) - startedAt`，运行中任务显示「进行中」。

### 4.2 本轮变更清单（P1②③ 结构化条件输出 + 控制台启动自选）

**VLM 结构化条件输出与默认否认提示词**
- `contracts.py`：`ConditionCheck` 新增 `evidenceTimeSec`；`ModelAnswer.conditions` 转为主输出。
- `results.py`：`POSTPROCESS_VERSION` v4→**v5**；`parse_answer(trigger)` 解析 conditions + 证据校验降级；新增 `decision_from_conditions` 合取真值表；`apply_answer` 映射 `frameIndex`；`verified_decision` 精简为 advisory。
- `prompting.py`：`PROMPT_VERSION` →**v6**；去衣着/外貌禁令、默认否认、字段顺序 `videoDescription/conditions/decision/confidence/reason`、3 个 few-shot。
- `qwen_worker.py`：`parse_answer/apply_answer` 接线传 `trigger` + 采样清单；output 带 `eventCriteria` 快照。
- 前端 `utils/vlm.ts` + `ui/vlm/VlmPanel.tsx` + i18n：逐条件证据列表（状态标记 + 点击跳转 `evidenceTimeSec`）。
- 测试：`test_vlm.py` 更新、新增 `test_prompting.py`、合取真值表与证据降级覆盖。

**控制台启动自动选 CUDA + yolo11m-pose**
- `config.py`：`Settings` 新增 `autostart/default_model/device`（`ULTRA_AUTOSTART/ULTRA_DEFAULT_MODEL/ULTRA_DEVICE`）。
- `main.py`：FastAPI `lifespan`→`_autostart`（选 cuda/回退 cpu + 加载默认模型，失败降级不阻塞）；新增 `GET /api/state`。
- `state.py`：初始 `engine.device` 改用 `settings.device`。
- 前端 `contracts.ts`（`ConsoleBootstrap`）+ `client.ts`（`apiGetState`）+ `ControlPanel.tsx`（挂载同步 engine/model，下拉与按钮保留）。
- 测试：`conftest.py` autouse 关闭 autostart；新增 `test_autostart.py`（/api/state、优先 cuda、回退 cpu）。
- 验证：后端 pytest 53 通过 + 1 skip、ruff 通过、前端 build/lint/test 30 通过；重启后 `GET /api/state` 实测回显 `cuda` + `yolo11m-pose.pt`。

### 4.3 本轮变更清单（库记录命名 + 处理时长）

- **展示名 = 原视频名 + 分钟级时间戳**：`cli.py` 的 `prepare()` 创建任务时写 `run_dir/library.json` 的 `displayName = <fileName>_<YYYYMMDD_HHMM>`（如 `监控.mp4_20261105_1730`）；`library_api._entry()` 优先取 `displayName`，内部 `run_id`（`vlm-<uuid>`）仅作目录/URL/去重，不再作展示主标识。旧任务无 `library.json` 时仍显示原文件名。
- **计时器与处理时长**：`prepare()` 写 `status.json` 的 `startedAt`（用户点「开始分析」那一刻），`runner.run()` 改为沿用该值不重置；`library_api._entry()` 计算 `durationSec = finishedAt(或当前时间) - startedAt` 并随每条记录返回。
- 前端：`utils/library.ts` 的 `LibraryItem` 增加 `durationSec` 与 `formatDuration()`（时/分/秒、中英双语）；`LibraryPage` 新增「处理时长」列、`LibraryDetail` 显示时长，运行中任务标注「进行中」。
- 测试：新增 `test_display_name_and_duration`（displayName 时间戳 + durationSec 精确断言）。验证：后端 pytest 137 passed + 1 skip、ruff 通过、前端 build/lint/test 30 通过。

### 4.4 路径自包含（权重与解释器）

- **VLM 权重根目录**：`models.py` 的 `MODEL_ROOT` 原为 `parents[5]`（实际解析到 `/home/user`，即项目外），现改为项目内 `models/`，并支持 `VLM_MODEL_ROOT` 覆盖（容器里挂载到 `/models` 时使用）；2B/4B 权重已复制到 `models/Qwen3-VL-2B-Instruct` / `models/Qwen3-VL-4B-Instruct`，`VLM_MODEL_PATH` 仅作为单模型路径的覆盖入口。
- **VLM 解释器**：`cli.py` 的 `qwenPython` 默认改为 `sys.executable`，不再写死开发机 conda 路径；Qwen 跑在独立环境时用 `VLM_PYTHON` 覆盖（本机放 `backend/.env`，容器由编排注入）。
- `.gitignore` 忽略 `models/*`（保留 `.gitkeep`）；README 权重/解释器说明与 `backend/.env.example` 已同步。
- 验证：`/api/vlm/parameters` 返回项目内路径，2B/4B 均 `available`；后端 + Rules 测试通过。

### 4.5 交付物（deliverable/）

- 新增 `deliverable/`：`Dockerfile`（Node 构建前端 + 轻量 Python 运行时）、`compose/docker-compose.yml`（`setup` 一次性安装 + `app` 常驻）、`setup.sh`（把依赖装进 `ultra-envs` 卷、权重下载到 `/models`）、Windows 一键启动器（`.bat` + `.ps1`）、部署/运维/验收文档。
- 镜像**不含** PyTorch 与模型权重；首次 `docker compose run --rm setup` 安装并下载，之后启动直接复用卷。运行期只暴露 `8001`，`server.py` 把 `frontend/dist` 挂到 `/`，与 API 同源。
- `models.py` 新增 `VLM_MODEL_ROOT`：容器内设为 `/models`，权重发现仍能列出 2B/4B。
- 验证：`deliverable/test/check.sh` 通过（脚本语法、compose 结构、Dockerfile COPY 路径、BAT→PS1 引用、无写死家目录）；前端构建通过；后端 + Rules 测试通过。**镜像构建与 GPU 冒烟需在有 Docker 的机器上执行** `deliverable/build.sh` 与 `deliverable/test/gpu-smoke.sh`。

---

## 5. 核心问题：事件判定准确率

### 5.1 问题表现（一次实测）

- 某 20 分钟视频产出 **70 条候选 → 70 条全部判 `match`**，且置信度只有 0.85 / 0.95 两档。
- 其中 21 条是 `initial_inside`（首次出现已在区内，本就不满足“进入”）、25 条是 `zone.leave`（语义方向相反），仍全部判 `match`。
- 抽检全分辨率帧：门框**没有竖直红线、顶部是绿灯**，但模型输出反复写“门框有明显竖直红线亮起”——说明是**模型复述规则文字，而非真正观察画面**。
- 模型还把候选的触发时间当成红灯起始时间（违反提示词里的明确要求）。

### 5.2 根因分层

1. **证据不可见（最关键）**
   - 抽帧被缩到 `576×320`（`maxPixels=196608`，线性缩小约 0.3 倍），一条 5px 的红灯只剩约 1.5px，模型根本看不清。
   - 采样 `2 FPS / 最多 32 帧`，短闪的红灯可能整段漏掉。
2. **规则语义不可直接判定**
   - 原窗口以“进入区”触发点为基准，而不是以“红灯亮起”时刻为基准；红灯延迟亮起时，5 秒窗口对不齐。
   - `initial_inside` / `zone.leave` 等触发类型与“进入 + 未退回”的事件语义不匹配。
3. **模型与提示词偏差**
   - 2B 模型容量不足，倾向于把 `criteria` 复述成“已发生”。
   - 提示词含“未看见不等于没发生”，把小模型推向“看不清也判 match”。
   - 置信度是模型自报、未校准，只有两个值，不能当概率用。
4. **后处理不拦截**
   - `model-decision-v4` 保留模型结论；质量警告只提示不否决；分段聚合是“任一段 match 则整体 match”。
5. **没有评测集 / 指标**，无法量化“改好了没有”。

> P0 已解决第 2、4 层的大部分（红灯 CV 检测 + 确定性判定 + veto + 触发收紧）。**第 1、3、5 层是 P1 / P2 的核心。**

---

## 6. 改进路线图

### 6.1 P0 —— 确定性判定（✅ 已完成）

**做了什么**

- 新增 `CueDefinition`（信号区几何 + 红色阈值参数）与 `RunRequest.cues`、`EventDefinition.cueIds`。
- 新增 `Rules/src/ultra_rules/vlm/cues.py`：在绘制 ROI 内做红色掩码 + 滞回，输出红灯亮/灭区间；含独立缓存；`cli calibrate` 输出红占比时间序列、直方图、Otsu 建议阈值、峰值帧 RGB 统计。
- 新增 `Rules/src/ultra_rules/vlm/decision.py`：确定性判定（红灯状态 / 是否退回 / 轨迹是否连续），并做“进入—退回”配对抑制，避免一次进出被重复计数。
- `understand()` 改为确定性优先，Qwen 模型改为**懒加载**（纯确定性结果不加载模型）。
- 前端新增“画信号区”工具、“信号区阈值”面板、入口方向翻转；规则编辑器支持绑定信号区。

**如何校准红灯阈值（运维步骤）**

```bash
PYTHONPATH=Rules/src python -m ultra_rules.vlm.cli calibrate \
  --input <视频文件> --cue '{"id":"red","points":[[x1,y1],[x2,y2],...]}'
```

把输出的 `suggestedMinOnRatio` 等建议值填进前端「信号区阈值」面板。**注意**：校准需要一段“红灯确实亮起”的片段；若只有“灭”的样本，只能确定“灭”的基线上限，无法验证召回。

### 6.2 P1 —— 证据、提示词与模型（🔶 部分完成：②③ 已落地并 GPU 实测，①④⑤ 待做）

**目标**：让模型“看得见、说得出证据、判得稳”。

1. **高分辨率 ROI 抽帧**
   - 输入改为多路：全帧（上下文）+ 门框 ROI 原分辨率/2x + 目标裁剪。
   - 把 `maxPixels` 提高到 786432（或按 ROI 单独给预算）；红灯线宽 ≥3px、目标高度 ≥120px。
   - 围绕“红灯亮起时刻”以 4–6 FPS 抽 5–6 秒，`maxFrames` 提到 48–64（而不是固定 2 FPS 覆盖 8 秒）。
   - 相关代码：`Rules/src/ultra_rules/vlm/sampling.py` 的 `windows / choose_rows / build_sample`。

2. **结构化条件输出，代码做最终决策**（✅ 已完成）
   - 模型对每个 `criteria` 输出 `{index, status: yes/no/unknown, evidenceTimeSec}`；`results.py` 的 `decision_from_conditions` 按合取真值表算 decision（任一 no→no_match；全 yes→match；否则 uncertain），模型自由文本结论降级为 `modelDecision` 对照，两者不一致记 `condition_decision_mismatch`。
   - 证据校验：`evidenceTimeSec` 越窗 / 约等于 `triggerSec`（|差|<0.15s）/ 为 null 时把该条件降级 `unknown` 并记警告；`conditions` 缺失或数量不符则整体回退采信 `modelDecision`（`conditions_unavailable`），保证旧模型/坏格式不崩。
   - `apply_answer` 把 `evidenceTimeSec` 映射到最近采样帧 `frameIndex`（`sampleTimesSeconds`+`sourceFrameIndices`）供前端回放；`qwen_worker` output 带 `eventCriteria` 快照供前端按 index 映射条件文本。
   - **未做**：条件的 ROI/bbox 输出（2B 模型不稳定，依赖 P1①）。相关代码：`prompting.py`、`results.py`、`contracts.py`（`ModelAnswer/ConditionCheck`）、`qwen_worker.py`、前端 `utils/vlm.ts` + `ui/vlm/VlmPanel.tsx`。

3. **提示词改为“默认否认”**（✅ 已完成，`PROMPT_VERSION=target-event-described-json-v6`）
   - 删除“未看见不等于没发生”，改为默认否认：只有清晰看到条件所需现象才认定发生；固定 ROI 线索在采样完整覆盖下“未出现”作为 no 证据，仅遮挡/窗口外才 uncertain；红灯亮起时刻以画面为准，不得用 `triggerSec` 代替。
   - 禁止描述衣着/外貌/体型/发色等与判定无关属性（目标身份由粉红框标识）；`videoDescription` 收紧到 2–4 句、`reason` 逐项交代各必要条件的可见依据。
   - 输出字段顺序 `videoDescription, conditions, decision, confidence, reason`；加 3 个 few-shot（match / no_match / uncertain）。
   - **GPU 实测**：输出不再出现衣着词、`videoDescription` 字数降约 3–4 倍且关键动作/时序保留、conditions↔criteria 映射与合取决策生效、`evidence_time_equals_trigger` 降级在真实数据触发。

4. **模型升级与集成**
   - 至少用 4B 做对比（16GB 显存可跑 4B BF16，此前峰值约 9GB）；8B 需量化/卸载。
   - 对同一候选做 2–3 次不同帧子集/提示的 self-consistency，或加一个 critic 二次核验。
   - **不要指望只换大模型解决“红灯只有 1.5px”的问题——先修输入证据。**

5. **置信度校准 + 拒答阈值**
   - 在标注集上校准置信度（ECE / Brier），设“低置信度转人工”的阈值。

### 6.3 P2 —— 评测与生产化（⬜ 待做）

**目标**：用数据证明“达到上线标准”，并可持续运营。

1. **建立标注评测集**
   - 从历史任务里抽 300–500 条候选，按触发类型、遮挡、灯光状态分层；做双重标注。
   - 每条标注两个层面：**事件级 label** + **逐条件 label**（进入 / 红灯亮 / 5 秒内未退回）。
2. **指标（不要只看 accuracy，类别不均衡会失真）**
   - 事件级：precision / recall / F1 / PR-AUC、每小时误报数、abstain（不确定）率。
   - 条件级：尤其“红灯是否亮”的单条件准确率。
   - 置信度校准：ECE / Brier。
3. **上线验收门槛（建议值，需业务确认）**
   - 真实事件 recall ≥ 95%；precision ≥ 90%，或误报 ≤ 1 次 / 2 小时；abstain ≤ 10%，且不确定样本全部进人工复核；每条结果可回放到证据帧。
4. **人工复核闭环 + 监控**
   - 复核队列、纠错写回、按相机/机位监控红灯检测漂移；规则 / 提示词 / 模型版本化。

---

## 7. 开发者指引

### 7.1 关键代码地图

**后端（Python，`Rules/` 是纯算法库，`backend/` 是 FastAPI 服务）**

| 路径 | 职责 |
|---|---|
| `Rules/src/ultra_rules/vlm/contracts.py` | 全部数据模型（Poi / CueDefinition / EventDefinition / RunRequest 等） |
| `Rules/src/ultra_rules/vlm/models.py` | 本地 Qwen3-VL 权重发现与校验（默认根目录为项目内 `models/`，不读项目外路径） |
| `Rules/src/ultra_rules/vlm/observations.py` | 跟踪阶段：YOLO-Pose + ByteTrack → observations.sqlite |
| `Rules/src/ultra_rules/vlm/poi.py` | 候选生成：PoiTracker 状态机 |
| `Rules/src/ultra_rules/vlm/cues.py` | 红灯 CV 检测 + `calibrate()` 校准工具 |
| `Rules/src/ultra_rules/vlm/decision.py` | 确定性判定（红灯 / 退回 / 轨迹连续性） |
| `Rules/src/ultra_rules/vlm/sampling.py` | 抽帧窗口、选帧、标注预览图 |
| `Rules/src/ultra_rules/vlm/prompting.py` | VLM 提示词 |
| `Rules/src/ultra_rules/vlm/qwen_worker.py` | Qwen 模型封装 + `understand()` 编排 |
| `Rules/src/ultra_rules/vlm/results.py` | 解析 / 裁决（`decision_from_conditions` 合取真值表 + 证据校验降级）/ 聚合 |
| `backend/app/vlm_api.py` | VLM 相关 HTTP 接口 |
| `backend/app/event_rules.py` | 规则库 CRUD + ManagedRun + resolve |
| `backend/app/library_api.py` / `video_api.py` | 记录管理（展示名/处理时长 `durationSec`）/ 视频预览转码 |
| `backend/app/main.py` | FastAPI 入口：VLM/视频/库路由 + 实时推理接口（`/api/models` `/api/state` `/api/params` `/api/system/stats` `/ws/infer`）+ 启动 `lifespan` 自动选设备/加载默认模型 |
| `backend/app/infer_runtime.py` / `devices.py` / `state.py` / `config.py` / `system_stats.py` / `logging_store.py` / `schemas.py` | 经典 CV 实时推理运行时（模型加载、设备、参数、启动自选配置、系统状态、日志、Schema）|

**前端（`frontend/src/`）**

| 路径 | 职责 |
|---|---|
| `ui/viewer/tabs/VideoTab.tsx` | 视频选择 + 预览 + 基础检测分析 + 画线/区/信号区 + 挂载 VlmPanel |
| `ui/viewer/widgets/EventEditOverlay.tsx` | 画 POI（含入口方向箭头） |
| `ui/control/ControlPanel.tsx` | 实时控制台左栏（引擎/NMS/类别过滤/模型；挂载读 `/api/state` 回显启动自选的设备与模型） |
| `ui/monitor/MonitorPanel.tsx` | 实时控制台右栏（系统状态/配置/计数事件/告警/日志） |
| `ui/vlm/VlmPanel.tsx` | 语义分析控制 + 结果展示（含逐条件证据 + 点击跳转 `evidenceTimeSec`） |
| `ui/vlm/EventRulesEditor.tsx` | 规则编辑 + POI/信号区绑定 |
| `ui/vlm/CueSettings.tsx` | 信号区阈值编辑 |
| `ui/vlm/AnalysisParameters.tsx` | 分析参数（模型 / 姿态设备 / 抽帧等） |
| `ui/library/LibraryPage.tsx` | 记录与视频管理（单页内切换） |
| `store/useConsoleStore.ts` | 全局状态（引擎/参数/模型/预测/日志/告警/ROI/计数/事件/`eventsConfig`） |
| `store/useUiStore.ts` | 控制台 / 库 视图切换 |
| `utils/vlm.ts` / `eventRules.ts` / `vlmParameters.ts` | VLM 请求构造与类型 |
| `utils/videoAnalysis.ts` / `events.ts` / `roi.ts` / `useEventTracker.ts` / `useSystemStats.ts` / `draw.ts` / `configIO.ts` | 实时视频分析、事件追踪、ROI、画图、配置导入导出 |

### 7.2 运行与验证命令

```bash
# 后端 + 规则算法测试
cd <项目根目录>
PYTHONPATH=backend:Rules/src python -m pytest backend/tests Rules/tests

# 后端 lint
python -m ruff check backend/app Rules/src

# 前端类型检查 / 测试 / lint / 构建
cd frontend && npm run build && npm test && npm run lint

# 启动（后端 8001 / 前端默认端口，示例 5100）
PATH=<conda>/envs/ultraconsole/bin:$PATH ./start.sh 5100

# 模型权重位置（项目内，默认加载；不需要设置 VLM_MODEL_PATH）
# models/Qwen3-VL-2B-Instruct/   ← 默认模型
# models/Qwen3-VL-4B-Instruct/
# Qwen 独立解释器写在本机 backend/.env 的 VLM_PYTHON（不入库）

# 红灯阈值校准
PYTHONPATH=Rules/src python -m ultra_rules.vlm.cli calibrate \
  --input <视频> --cue '<信号区JSON>'
```

### 7.3 给后续 AI / 开发者的约定

1. **候选时间点已可靠**：不要改动 `poi.py` 的候选生成逻辑。
2. **红灯语义已定**：门框竖直红色 LED 灯带；顶部绿灯忽略；用 CV 检测，不是让 VLM 看。
3. **判定策略已定**：确定性优先，VLM 只复核不确定样本。
4. **退回语义已定**：入口线反向跨回，5 秒窗口从红灯亮起时刻算起。
5. **Schema 兼容**：给 `EventDefinition` 等新增字段时，`event_rules.py` 的 `resolve()` 里读取必须用 `.get()` 容错（历史 `event-rules.json` 可能缺新键）。
6. **先解释方案再动工**：涉及功能设计 / UI 交互 / 阈值语义等有多种路径的改动，先给方案让用户拍板。
7. **验收要能量化**：任何“准确率提升”都要有标注集 + precision/recall/F1 支撑，不能只凭样例感觉。
8. **资源路径必须自包含**：权重、配置、示例数据的默认路径一律落在项目内；解释器与外部挂载只能通过 `VLM_PYTHON` / `VLM_MODEL_PATH` / `ULTRA_MODELS_DIR` 等环境变量覆盖，代码里不得写死家目录或机器专属绝对路径。

---

## 8. 已知边界 / 风险 / 待办清单

- [ ] **红灯阈值仍是默认值**：需用真实“亮/灭”片段跑 `calibrate` 校准，尤其 `minOnRatio / minR / minRG / minRB`。
- [ ] **入口方向**：默认双向 + 退回配对；单向闸门若仍出现“退回被当新进入”的边界，用前端“进入方向”翻转控件设定方向。
- [ ] **红灯起始时刻 vs 触发窗口**：红灯延迟亮起超过 `preSeconds/postSeconds` 时，5 秒窗口会对不齐（P1 以 onset 定窗口后可解决）。
- [ ] **置信度不可信**：当前是模型自报、未校准，不能作为概率或阈值。
- [x] **CUDA 端到端**：本机有 GPU，已验证控制台启动自动选 cuda + 加载 `yolo11m-pose.pt`（`GET /api/state` 回显），并跑通一次真实 VLM 分析（`model-decision-v5`）。
- [ ] **P1② 证据降级 vs 缺失即否定**：`missing_evidence_time` 会把“基于未出现”的 no（`evidenceTimeSec=null`，如“红灯从未亮起”）降级为 unknown，使 `no_match`→`uncertain`，与目标“未出现作为 no 证据”存在张力；待决定是否放宽（`status=no` 且 null 不降级，仅 `yes` 且 null 才降级）。
- [ ] **规则 criteria 混入指令型条目**：如“忽略安检门顶部灯光”不是可检验条件，合取（全 yes→match）下模型无法标 yes 会阻断 match；需在规则库层面把“指令”与“可检验条件”分开。
- [ ] **采样 manifest 长度不一致**：个别窗口 `sampleTimesSeconds` 与 `sourceFrameIndices` 长度不等时 `frameIndex` 映射被跳过（前端 seek 用 `evidenceTimeSec` 不受影响）；待排查 `sampling.py` 或加防御性对齐。
- [ ] **评测集缺失**：P2 前无法量化准确率，属于当前最高优先级的“基础设施”缺口。

---

## 9. 下一步建议（按顺序）

1. **P2 先行打地基**：先做最小标注集 + 指标脚本（哪怕只标 100 条），否则 P1 的每次调整都无法证明好坏。
2. **P1 的“证据”优先于“换大模型”**：先做高分辨率 ROI 抽帧 + 结构化条件输出，再做 4B/8B 对比。
3. **红灯阈值校准**：找一个含真实红灯亮起的片段，跑 `calibrate` 落地阈值。
