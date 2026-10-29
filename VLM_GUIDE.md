# UltraConsole：POI 触发的 Qwen3-VL 离线视频理解开发指导

版本：v1.0 · 2026-10-23 · **原审阅稿；首版现已实施，实际交付与验证见 [VLLM_IMPLEMENTATION.md](VLLM_IMPLEMENTATION.md)。**

本轮只新增本文档。方案依据当前工作区代码、已有 Rules 实现及本机 Qwen 原型制定，包含尚未提交的项目进度；旧方案中的“尚未实施”不代表当前代码状态。

## 1. 首版要交付什么

在 UltraConsole 中导入视频，画好 POI 区域或线，配置一组自然语言事件描述，启动一次分析。系统完成：

1. YOLO11 Pose + ByteTrack 获取每个人物的轨迹、bbox、COCO17 关键点与视频时间。
2. 后端检测人物进入／离开区域或穿越线段，生成与人物 ID 绑定的候选事件。
3. 为每个候选事件选取前后视频窗口，抽取带目标标记的有序视频帧，同时保留其他人的动作和场景。
4. 使用已有本地 Qwen3-VL-2B-Instruct，逐条判断是否符合配置的事件描述。
5. 返回人物 ID、POI、时间、匹配／不匹配／证据不足、简短依据、模型自报置信度和证据预览。
6. 结果落盘，可在页面查看并跳回源视频对应时间；失败项可定位，不因一个片段失败丢掉整次分析。

**首版按完整离线任务执行，允许慢于播放速度。先扫描轨迹和触发事件，再集中运行 Qwen，不要求人在视频里跨线的瞬间就出语义结论。**

`VLLM.md` 是按要求命名的指导文件。本文的视觉语言模型简称 VLM；首版采用已经跑通的 Transformers 推理方式，不把安装 vLLM 推理框架作为前置条件。

首版包括最小上传、配置、任务状态、结果展示；告警弹窗、Webhook、日志大屏、生产调度、模型微调、跨镜头 ReID 和置信度校准留待闭环跑通后改善。

## 2. 已核实的项目基础

| 位置 | 当前实际能力 | 本次如何使用 |
|---|---|---|
| `frontend/src/ui/viewer/tabs/VideoTab.tsx` | 浏览器加载本地 File，以视频时间顺序抽 JPEG，通过独立 WS 跟踪；默认 8 FPS | 复用视频选择、预览和画线／画区；增加独立的“语义分析”入口 |
| `frontend/src/utils/events.ts` | bbox 中心点计数；前端有限线段穿越；zone.enter / zone.leave | 参考事件名称和现有交互，新任务由后端计算触发 |
| `frontend/src/api/contracts.ts`、`store/useConsoleStore.ts` | `trackId`、bbox、keypoints、归一化 CountingLine / CountingZone | 增加新任务契约，并转换已有 POI 配置 |
| `backend/app/infer_runtime.py` | `track(persist=True, tracker="bytetrack.yaml")`；按 tracker_key 隔离；bbox 和关键点来源索引对齐 | 保留在线链路，离线复用同一权重和数据含义 |
| `Rules/src/ultra_rules/pose_adapter.py` | 独立 YOLO 实例，输出 PersonObservation；每视频重置跟踪状态 | 作为离线 Pose 适配基础，避免重写模型推理 |
| `Rules/src/ultra_rules/contracts.py` | FrameObservation 已有 runId、videoId、frameIndex、mediaTimeSec、persons、quality | 扩展为新链路可复用的观测基础 |
| `Rules/src/ultra_rules/video_reader.py` | OpenCV 顺序解码、时间回退、采样、证据单帧读取 | 新链路补齐准确采样日程和解码时间映射 |
| `Rules/src/ultra_rules/cache.py` | JSONL.gz 观测缓存、模型和参数指纹、原子写入 | 保存结构化观测，改文本描述时不重跑 Pose |
| `Rules/src/ultra_rules/features.py`、`quality.py` | 有效关键点、足点、躯干、腕耳距离、角度和质量线索 | 形成有缺失标记的辅助证据 |
| `Rules/src/ultra_rules/workflows/passage.py` | 通行／检查区／交互流程推断，依赖岗位驻留等先验 | 可作为独立辅助线索，不能直接当作 VLM 标准答案 |
| `backend/app/rules_api.py`、`frontend/src/ui/rules/RulesTab.tsx` | 触发 CLI、读取磁盘报告、播放器／时间轴／截图 | 复用组织方式及 UI 组件思路，新任务使用明确的生命周期 |
| `Rules/configs/checkpoint.json` | 已有 d2、c2 机位区域，默认 Pose 为 CPU、目标采样 10 FPS | 可作为样本联调起点；用户新上传视频使用实际所画 POI |

当前尚不存在“POI → 按人抽帧 → Qwen 事件分类”的完整链路。尤其 Video 页的 `blob:` 地址仅在浏览器有效，后端拿不到源文件；必须增加真正的视频上传，不能把文件名或浏览器地址传给 Python 当路径。

### 2.1 需要在新链路内处理的现有细节

- **前后端空间语义不同**：Video 计数用 bbox 中心，Rules 越线使用脚踝／bbox 底边中心。新 POI 明确保存 `anchor`；从 Video 导入的 POI 默认 `bbox_center`，保持用户当前画线预期。可选 `bbox_bottom_center` 用于地面区域；首版不自动在多个锚点之间切换。
- **后端有限线段判断不完整**：`AccessCrossingDetector.update()` 只检查侧别变化，未实际约束穿越有限线段；`hysteresisPx` 比较的是未经线长归一化的叉积。新触发器采用轨迹段与有限线段相交，以及叉积除以线长后的像素距离。
- **前端缺失状态立即清除**：`events.ts` 的注释提到保留少量帧，实际本帧未出现就删除状态；重新出现可能产生重复进入。新后端触发器维护按秒计的缺失宽限。
- **首次出现在区内**：当前计数直接计入进入。新语义链路记录 `initial_inside`，默认不等同真实 `zone.enter`；可显式配置触发，但要标记缺少进入前文。
- **采样时钟**：现有 Rules 按“距离上一实际采样时间”判断间隔，25 FPS 视频设置 10 FPS 可能实际每 3 帧取一次。新链路按 `start + k / targetFps` 的目标时刻选帧，报告目标与实际采样时间。
- **任务结束判断**：现有 Rules API 根据 `run.json` 是否出现判断结束，未持续保存子进程返回码。新任务必须区分运行、失败、取消和部分完成。
- **旧缓存含旧 runId/profileId**：复用观测时重绑定当前任务上下文；POI 触发必须按当前配置重新计算。
- **缓存与证据**：现有扫描会把全部观测积累在列表中，部分截图调用早于源视频引用建立。新链路流式存观测、显式传递源视频与时间，不照搬这些行为。

这些修正限定在新闭环必需的接入点；现有实时推理和旧报告继续保持可用。

## 3. 已核实的 Qwen 环境与接入选择

| 项目 | 本机依据 |
|---|---|
| 模型权重 | `/home/user/Qwen3-VL-2B-Instruct`，对应用户提供的 WSL 路径 |
| 已跑通原型 | `/home/user/qwen3vl-prototype/recognize.py` |
| 推理 Python | `/home/user/miniconda3/envs/qwen3vl-bf16/bin/python` |
| 原型依赖记录 | Transformers 4.57.6、Accelerate 1.12.0、PyAV 16.0.1；复用现有环境，不主动升级 |
| 原型加载方式 | `Qwen3VLForConditionalGeneration` + `AutoProcessor`；BF16、SDPA、`local_files_only=True` |
| 原型视频输入 | RGB 帧数组 + `video_metadata`；`do_sample_frames=False`、`do_resize=False` |
| 当前 GPU 检查 | NVIDIA GeForce RTX 3080 Laptop GPU，16384 MiB 显存；检查时显存使用为 0 MiB |
| 已有验证记录 | `outputs/batch_20261023_092722/summary.json`：7 个视频、20 个分段 complete，最大 PyTorch allocated 约 4.57 GiB |

上述批处理数据来自已有结果文件，本轮没有重新运行模型。它们证明基础推理已跑通，**不证明新事件判别准确，也不代表集成后的显存和耗时**。

首版复用原型中的模型加载、视频输入与生成设置，将需要的逻辑移入本项目可维护的模块；不要求运行时依赖另一个工程的 Python 源文件，不复制权重，不修改原型与既有环境。相关类及视频时间处理可对照 [Qwen 官方模型说明](https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct) 和 [Transformers Qwen3-VL 文档](https://huggingface.co/docs/transformers/model_doc/qwen3_vl)。具体实现以本机已验证的 4.57.6 接口为准。

### 3.1 进程与显存安排

采用后台协调器 + 两个顺序执行的阶段进程：

```text
Video 页面：上传视频、POI、文本事件集
                ↓
FastAPI：校验、保存任务快照、启动协调器、返回 runId
                ↓
阶段 A（UltraConsole Python）
解码 → YOLO Pose / ByteTrack → 观测缓存 → POI 候选与窗口清单
                ↓ 阶段 A 子进程退出，释放其 CUDA 上下文
阶段 B（qwen3vl-bf16 Python）
读取窗口 → 抽帧／目标标记 → Qwen 按人判断 → 校验与逐项落盘
                ↓
页面轮询任务状态 → 展示结果与证据 → 跳回原视频
```

每个任务的阶段 B 只加载一次 Qwen，顺序处理所有候选。首版最多运行一个新语义分析任务，不搭 Redis/Celery，不启动多份 Qwen，也不以“每个人调用一次旧 CLI”的方式反复加载权重。没有候选时不加载 Qwen。

阶段 B 的模块不导入 YOLO 适配器及 Ultralytics，避免强迫两套 Python 环境合并。跨进程以 JSON 清单和磁盘产物交接；进程内仅使用帧数组。

现有 Web 服务仍可能持有自己的在线 YOLO 权重，分阶段退出只能释放离线进程显存。新任务记录可用显存和实际峰值；显存不足时保留候选及结果，给出可重试状态，不终止其他会话，也不自动切换精度。首版验收先在无并行实时推理的条件下完成，再检查共存。

## 4. POI、人物身份与事件窗口

### 4.1 POI 配置与触发状态机

POI 坐标统一为相对原始解码画面的 `[0,1]`；前端显示缩放／留黑边不进入计算。新任务保存分辨率、方向处理和 POI 快照，预览与后端解码必须对齐。

| 配置 | 首版行为 |
|---|---|
| `zone` | 支持矩形转换的四点多边形或一般多边形；触发 `zone.enter`、`zone.leave` |
| `line` | 有向有限线段；触发 `line.cross`；保存 `negative_to_positive` / `positive_to_negative` |
| `anchor` | 默认 bbox 中心，可选 bbox 底边中心；整个任务保持一致 |
| 边界防抖 | 初始建议 0.2 秒稳定状态 + 按分辨率缩放的 6 像素死区；参数可配置 |
| 缺失宽限 | 初始建议 1.0 秒；短时漏检不触发离开，超时记 `track_lost` |
| 区内初见 | `initial_inside`，默认仅记录；可由事件配置显式订阅 |
| 方向 | API 保存几何方向，UI 按线端点顺序显示；不直接赋予“进门／出门”业务含义 |

线的“稳定侧”与“最近确认的穿越候选”配合保存：穿越死区后确认，不能因为中间数帧在线上就丢失真实跨线。端点擦边、零长线、沿线移动、延长线跨越、短时漏检和真正往返均有明确测试。

区域的进入／离开以观察到稳定的区外／区内转换为准。离开画面或跟踪丢失只说明观测中断，不能直接制造 `zone.leave`。

### 4.2 人物 ID 的作用域

主体使用 `(runId, videoId, trackId, trackSegmentId)` 标识；界面显示 `#trackId`。每个视频新建 ByteTrack，禁止跨文件沿用状态。

同一 ID 长间隔重现时拆成新的 trackSegment，不跨断点自动连接动作。ByteTrack 发生 ID 切换时，首版记录缺失／可疑跳变，不声称已识别真实身份，不擅自把相似衣着的两个人合并。没有有效 ID 的检测仍可作为周围场景信息，但不生成“某个人 ID”的判断任务。

“按每个人物 ID 抽帧”具体实现为**按人物轨迹段的每次 POI 事件抽帧**。同一人可以多次进入，不能全视频只给这个人一次判断。

### 4.3 时间窗口与去重

建议初始参数：触发前 4 秒、触发后 6 秒，单窗口最长 16 秒。它们是联调起点，可以由事件描述配置覆盖。

- 默认每个稳定进入、离开或跨线各生成一个 candidate，保留 triggerId、triggerSec、方向、轨迹段和 POI。
- 同一人物、同一触发类型、同一 POI 的抖动由状态机消除；不能用一个长冷却期吞掉真实往返。
- 首版不同真实触发分别输出结果。相同帧集合可复用解码产物，内容完全相同的模型请求可复用结果；不因窗口重叠而丢弃触发记录。
- 视频开头／结尾截断记录 `preContextClipped`、`postContextClipped`。模型只能对现有窗口判断。
- 超过 16 秒的配置窗口拆成带重叠的子窗口；按 2026-10-26 的业务要求，任一片段模型输出 match 则整体保留 match；否则有 uncertain 则整体 uncertain；全部 no_match 才整体 no_match。说明、置信度与预览取自支持汇总结论的首个片段，不取最高分或平均分。
- 身份连续性、人物是否可见和旁人上下文均按窗口记录；不能把跟踪丢失后的画面当作目标仍在场。

## 5. 抽帧与多源证据怎么结合

### 5.1 分开设置两种采样率

Pose 跟踪目标采样率初始 10 FPS；Qwen 初始 2 FPS、最多 32 帧。低频 VLM 输入不反过来限制跟踪频率。

Qwen 先在窗口中均匀选帧，再保留触发附近及窗口边界的代表帧；在 32 帧预算内替换低优先级均匀帧。帧按真实视频时间排序、去重，最终不足偶数时只复制末帧，并记录 padding。超过预算时保持全窗口覆盖，不只取前 32 帧。

首版优先从阶段 A 已有观测对应的源帧中选择，按帧索引／实际 PTS 重新解码，以保证目标框和像素是同一时刻。不能把 10 FPS 观测中的某个框直接画在另一张 2 FPS 解码帧上。无法精确配对时不画猜测框，记录 `unaligned_frame` 并影响证据完整性。

### 5.2 时间元数据

记录 `sourceFrameIndex`、`sourcePtsSec`、`mediaTimeSec`、`timeSource` 和实际选中时间。所有业务时间统一为“从视频起点开始的秒数”；非零容器起始 PTS 需减去起点。

本机 Transformers 4.57.6 会通过 `video_metadata.frames_indices / fps` 生成时间标记，并对两帧时序分组取平均。当前原型使用 `round(t * fps)`，固定帧率下有量化误差。新适配层保留真实时间，校验模型实际收到的时间标记：固定帧率使用源帧索引；可变帧率需用经测试的时间映射构造 metadata，并保存映射规则，不能将采样序号除以原始 FPS。

继续使用预抽帧输入并关闭处理器二次抽帧。测试至少覆盖 25／30 FPS、非零起始时间、可变帧率、奇数帧 padding；时间轴不可靠时标记质量问题，不输出精确动作先后结论。

### 5.3 模型实际看什么

首版默认输入**保留全画面的目标标记视频帧序列**：目标框使用固定醒目颜色并标 `TARGET #id`，邻近人物可用较淡标记；POI 只画细线。标注不得遮挡手部、门体和交互区域；缺失帧不延用上一张目标框。

这样每个人有自己的输入请求，同时模型仍能看到“是否有人阻拦”。只裁切目标人体会删除判断所需的旁人，不能作为此类事件的唯一输入。

默认沿用每帧像素预算 196608，保持比例并按模型要求对齐。1080p 全景中的小人物可能缩得过小；联调检查目标在模型输入中的实际尺寸，必要时提高预算到 393216，或增加覆盖目标、POI、互动人员的**固定场景裁剪**模式。裁剪范围整个片段固定，保存坐标变换与上下文截断标记；裁掉潜在阻拦者时不能得出“无人阻拦”。第一阶段先验证全景方案，再以证据预览决定是否启用裁剪。

完整 COCO17、bbox、检测置信度、角色来源、帧质量都保存在观测中。送给 Qwen 的文本只提供紧凑摘要及关键时间点，避免把每帧全部坐标填满上下文：

| 数据 | 用法 |
|---|---|
| trackId、bbox | 锁定目标、画框、计算可见覆盖率和轨迹 |
| COCO17 与点置信度 | 提取腕部靠近、姿态变化、有效关键点比例；缺失保持 unknown |
| 速度、停留、方向 | 用视频时间和像素／体尺度计算，不把无标定的像素速度写成米每秒 |
| 附近人物轨迹、距离、bbox 重叠 | 辅助定位交互对象；重叠不能直接证明拦截、搜身或接触 |
| 岗位区域与驻留 | 作为角色先验，保留 `roleSource`；驻留不是保安身份真值 |
| 既有 PassageWorkflow | 可供调试对照；首版不把其“未检查”等结论写进模型问题诱导答案 |
| 原始画面 | 判断可见动作和场景的主要依据 |

不会因为“能取到”就把所有数值无选择地塞给 2B 模型，也不会把 Pose 检测置信度当作业务事件置信度。

## 6. 文本事件描述合集

新增独立 `Rules/configs/vlm_events.json`。每条至少包含稳定 `id`、`description` 和 `enabled`；简单文本列表由 UI 补齐 ID，用户不必写 Python。可选字段用于约束 POI、触发类型、窗口及判定标准。

以下是建议配置样例，坐标需要用户在实际视频上绘制，不能把示例机位套用于所有录像：

```json
{
  "version": "1",
  "events": [
    {
      "id": "forced_passage_unopposed",
      "enabled": true,
      "description": "是否该人员强行通过安检门且无人阻拦",
      "poiIds": ["gate"],
      "triggerKinds": ["line.cross", "zone.enter", "zone.leave"],
      "preSeconds": 4,
      "postSeconds": 6,
      "criteria": [
        "目标人员实际通过了指定安检门或通道",
        "画面有明确的强行通过动作证据，而不只是快速正常通过",
        "相关时段和周围区域可见，未观察到其他人的阻拦动作"
      ],
      "uncertainWhen": [
        "无法区分目标人员",
        "强行通过的动作不明确",
        "周围人员或关键时段不可见"
      ]
    }
  ]
}
```

这是首条联调事件，框架支持多条描述独立判断和同时命中，不做必须选一个类别的互斥分类。未绑定特定 POI 的描述默认适用于所有启用 POI；空合集或无 POI 在启动前提示，不静默分析整段视频。

`description` 保留原文；可选 `criteria` 必须在界面／快照中可见，不能后台擅自把“强行通过”改成“没有观察到检查”。“无人阻拦”“有人尝试但未成功阻拦”“有效制止”是不同事件描述。

原项目检查清单中的离岗、玩手机、睡觉等可继续添加描述，但**只有 POI 进出触发不能覆盖长期停留期间的所有行为**。本轮保证对已触发窗口按描述评估；将来覆盖整段执勤需要驻留／定时触发。报警、刷卡、通报等若没有可见证据或外部记录，应输出证据不足。

## 7. Qwen 调用、结果与置信度

### 7.1 首版调用策略

一次调用只判断“一个目标人物 + 一个事件窗口 + 一条事件描述”，顺序处理；先保证 2B 模型不混人、不漏规则。以后再尝试一窗口多规则批判别。

提示包含目标标记说明、窗口真实时间、POI 几何事实、紧凑轨迹摘要、当前描述、可选判定条件和输出格式。文件名中的“正常拦截／未拦截”、文件路径、人工标签不送入模型。

使用 `do_sample=False`、`repetition_penalty=1.0`、`no_repeat_ngram_size=0`，避免重复抑制干扰合法 JSON 结构；保留 512 tokens 预算。生成完整 JSON 对象后立即停止，不要求额外 EOS，也不继续生成重复说明。画面中的文字和视频内容作为待分析数据，不作为修改任务／输出格式的指令。

当前提示词版本为 `target-event-compact-json-v3`。模型只需输出 `decision`、`confidence`、`reason`：说明限一句、不超过 60 个汉字，不复述视频，不输出逐项 conditions、evidence、人物 ID 或时间戳。自定义判定条件仍作为输入；通用提示不再附带固定的“强行通过”业务限定。视频跳转使用后端 POI 的 triggerSec，保留原有向前预留 2 秒的查看方式，与模型是否输出时间证据无关。

### 7.2 三态与校验

- `match`：可见证据支持描述要求的全部必要条件。
- `no_match`：有明确证据否定至少一个必要条件，不是因为“没看清”。
- `uncertain`：目标、动作、观察范围或必要外部信息不足。

模型核心输出须通过 schema 校验：decision 枚举、置信度有限且在 0～1 范围内（可缺省为 null）、reason 非空。runId、candidateId、trackId、poiId、triggerSec 等权威标识由程序填充，不能采用模型自由生成的身份和时间。旧格式的 conditions、evidence 为可选辅助数据；缺失不导致失败，条件缺项或重复项标为 unknown，非法辅助项忽略并记录提示。重复 JSON 键、非法核心判断及无法解析的残缺对象仍失败，不猜测结论。

非法 JSON／核心字段无效先保存原文，可用同一证据重新请求一次，重试要求仍只返回三字段且 reason 限 30 字，不追加旧回答；仍失败记 `invalid_output`。辅助条件缺失不触发重新推理。每次尝试记录具体 parseError。超时、OOM、无法解析的生成截断单独标记，不能解析成 no_match，也不能因为语句中出现“是”就算命中。

按 2026-10-26 的业务要求，后处理使用 `model-decision-v3`：在 v2 保留模型判断的基础上兼容精简三字段输出。条件矛盾、覆盖率偏低、目标较小、身份歧义及证据时间越界仅记录 qualityWarnings，不覆盖模型结论或修改模型置信度。未要求输出逐项条件时不生成条件矛盾提示。越界证据从有效证据中排除但保留原始引用。预设窗口首尾截断、时长不足和窗口边缘观测缺失仅保留元数据，不作为可信度因素。核心格式错误和推理失败仍保留独立失败状态，不伪造 match。

### 7.3 输出契约示例

以下数值仅展示契约，不是实际识别结果：

```json
{
  "schemaVersion": "1",
  "runId": "vlm-example",
  "videoId": "video-example",
  "candidateId": "candidate-001",
  "trackId": 17,
  "trackSegmentId": "17-1",
  "poiId": "gate",
  "triggerKind": "line.cross",
  "triggerSec": 8.4,
  "window": {"startSec": 4.4, "endSec": 14.4},
  "eventDefinitionId": "forced_passage_unopposed",
  "inferenceStatus": "succeeded",
  "modelDecision": "uncertain",
  "decision": "uncertain",
  "modelDecisionConfidence": 0.76,
  "confidenceType": "model_self_reported_uncalibrated",
  "reason": "目标通过通道，但无法从当前画面确认强行通过动作。",
  "evidence": [{"timeSec": 8.4, "observation": "目标位于通道边界附近"}],
  "quality": {
    "targetVisibleFraction": 0.9,
    "identityAmbiguous": false,
    "preContextClipped": false,
    "postContextClipped": false,
    "contextTruncated": false
  },
  "downgradeReasons": [],
  "qualityWarnings": [],
  "postprocessVersion": "model-decision-v3",
  "sampleManifest": "samples/candidate-001.json",
  "rawOutputPath": "raw/candidate-001-forced_passage_unopposed.txt"
}
```

`targetVisibleFraction` 按不重复的实际输入帧计算，padding 不增加覆盖率；姿态可用率另列，避免把缺关键点等同于目标不可见。

**置信度语义：** `modelDecisionConfidence` 是模型对自己三态判断的自报把握程度，0.76 不等于有 76% 概率发生违规；uncertain 也可以有较高的自报把握。界面显示“模型自报置信度（未校准）”。缺省分数保存 null；后处理不调整模型分数，多片段时显示所选片段的原始分数。

首版不输出伪精确的“综合事件概率”，不把检测框分数、token 概率或多次模型回答频率直接转换成业务准确率。后续积累事件级人工标注，再单独做分数校准及阈值评估。

## 8. 后端接口、落盘与最小页面

### 8.1 建议接口

新路由前缀 `/api/vlm`，沿用项目已有鉴权和 `/api` 代理：

| 接口 | 用途 |
|---|---|
| `POST /api/vlm/videos` | multipart 流式保存浏览器 File，返回 videoId 和解码元信息 |
| `POST /api/vlm/runs` | JSON 提交 videoId、POI、文本事件集、采样参数；立即返回 runId |
| `GET /api/vlm/runs` | 列出任务，页面刷新可恢复 |
| `GET /api/vlm/runs/{runId}` | 状态、阶段、进度、候选／调用数量、错误摘要 |
| `GET /api/vlm/runs/{runId}/results` | 分页读取结果，按 trackId、事件描述、decision 过滤 |
| `POST /api/vlm/runs/{runId}/cancel` | 停止协调器及其阶段进程，保留已完成产物 |
| `GET /api/vlm/runs/{runId}/artifacts/{path}` | 读取证据图和采样清单，严格限制在对应任务目录 |
| `GET /api/vlm/videos/{videoId}/source` | 播放源视频，支持 Range 和正确媒体类型 |

videoId 由服务器生成并映射上传文件，任务 ID 使用 UUID；上传流式限额、校验文件可解码，错误上传不留下可用条目。请求不能指定任意系统文件路径或执行程序，进程启动用参数数组。新增文件访问校验使用路径包含关系，不照搬字符串前缀比较。

状态为 `queued → tracking → understanding → completed / partial / failed / cancelled`。取消是实际停止工作；重新打开页面不重复提交任务。服务重启后未完成任务明确标记 interrupted，可从已保存阶段产物重新发起后续处理，首版不承诺自动续跑。

`completed` 表示所有计划调用有合法结果，包含 uncertain；`partial` 表示部分调用失败；`failed` 表示关键阶段失败；零候选正常完成但返回 `no_candidates`。状态由子进程退出码和原子写入的 status 文件共同确定，不依赖日志内容猜测。

### 8.2 建议产物结构

```text
Rules/data/vlm/
  uploads/<videoId>/source.<ext>
  cache/<observation-key>.jsonl.gz
  runs/<runId>/
    status.json
    request.json                 # POI、描述、所有参数快照
    manifest.json                # 源文件标识、模型、依赖、代码/提示版本
    observations.ref.json        # 指向本次使用的观测缓存
    candidates.jsonl
    results.jsonl                # 每个候选 × 每条适用描述一条记录
    summary.json
    samples/<candidateId>.json   # 实际帧索引、PTS、标记与缩放映射
    previews/<candidateId>.jpg
    raw/<requestId>.txt
    errors.jsonl
```

原始观测使用 gzip 流式写入，完整后原子提交；候选和结果逐条落盘。帧像素只按当前候选有界保留，不把整段视频放入 RAM；预览图不等于模型实际输入帧数量，清单记录全部输入帧。

观测缓存指纹至少包含完整源视频内容标识、YOLO 权重标识、检测参数、实际 tracker 配置、采样及时间策略版本。当前“文件头 + 大小”可用于旧报告展示，但新缓存不据此认定内容完全一致。上传时流式计算文件哈希。

VLM 请求指纹另包含候选时间、实际帧、视觉标注／裁剪、目标轨迹段、POI、事件文本、prompt、模型／processor 版本及生成设置。改描述只重新做语义判断，改 POI 重建候选，改 Pose 参数才失效观测缓存。首版可先不跨任务复用 VLM 答案，但每次必须记录请求指纹。

### 8.3 页面最小改动

在 Video 页增加“语义分析”入口：保留所选 File → 画 POI → 编辑／导入文本事件集 → 点击开始 → 上传并提交任务。原浏览器基础计数按钮继续可用，语义任务不同时启动另一套浏览器跟踪，不把两次运行中的相同数字 ID 当同一人。

新任务配置提交后冻结快照；后续修改画线或描述只影响下一次任务。首版锁定为 YOLO11 pose 权重与独立离线参数，避免用户误选 detect 权重却以为有 COCO17。

结果面板显示阶段进度及“人物 ID、触发 POI／时间、事件描述、判断、模型自报置信度、依据”，可展开证据预览并跳到源视频。明确区分“未发现 POI 触发”“全部不匹配”“证据不足”和“执行失败”。页面风格沿用现有控件，本阶段不做通知系统和布局大改。

## 9. 计划修改的位置

以下是施工定位，可按实现合理合并文件，职责保持明确：

| 文件／目录 | 工作 |
|---|---|
| `Rules/src/ultra_rules/vlm/contracts.py` | POI、描述、候选、任务与三态结果 schema |
| `Rules/src/ultra_rules/vlm/poi.py` | 有限线段、区域转换、防抖、缺失与轨迹段 |
| `Rules/src/ultra_rules/vlm/observations.py` | 复用 PoseAdapter，流式观测、缓存、采样时钟和候选清单 |
| `Rules/src/ultra_rules/vlm/sampling.py` | 源帧重取、时间配对、预算、标记、预览与输入清单 |
| `Rules/src/ultra_rules/vlm/prompting.py` | 一人一窗口一描述的提示与证据摘要 |
| `Rules/src/ultra_rules/vlm/qwen_worker.py` | 使用独立 Python 环境，单次加载 Qwen、顺序处理请求 |
| `Rules/src/ultra_rules/vlm/results.py` | 严格解析、质量提示、保留模型判断、多片段汇总 |
| `Rules/src/ultra_rules/vlm/cli.py`、`runner.py` | 无模型 dry-run、两阶段协调、取消／退出状态 |
| `Rules/configs/vlm_events.json` | 默认示例描述与版本 |
| `backend/app/vlm_api.py`、`vlm_jobs.py` | 上传、任务调度和产物读取 |
| `backend/app/main.py`、`config.py` | 注册路由、模型路径、Qwen Python、资源上限 |
| `frontend/src/api/client.ts`、`contracts.ts` | 新 API 与类型 |
| `frontend/src/ui/viewer/tabs/VideoTab.tsx` | File 保留、POI 快照和语义任务入口 |
| `frontend/src/ui/vlm/`、相关 i18n 文件 | 最小配置与结果面板 |
| `Rules/tests/`、`backend/tests/`、前端测试 | 几何、时序、输入输出及任务生命周期验证 |

模型路径与 Python 路径使用配置项，例如 `VLM_MODEL_PATH`、`VLM_PYTHON`；默认指向本机已有位置。UltraConsole Python 和 Qwen Python 分别核验，只记录并补充确实缺少的小依赖，不为此升级整套 Torch/CUDA。

2026-10-26 补充：页面“分析参数 → 语义分析模型”可选择本机 Qwen3-VL 权重。`modelPath` 随任务请求传递，优先于 `VLM_MODEL_PATH` 默认值，并保存到实际工作进程配置和历史参数快照。目前提供上级目录的 `Qwen3-VL-2B-Instruct`、`Qwen3-VL-4B-Instruct`，默认 2B。提交实际推理前检查模型架构、配置、分片索引及权重文件存在性；仅抽帧预览无需模型权重。模型选择不改变候选划分、抽帧参数、后处理判断或事件时间定位。

## 10. 实施顺序与每一步的完成标准

| 阶段 | 开发内容 | 必须看到的结果 |
|---|---|---|
| P1 数据和 POI | 配置契约、独立离线观测、可靠时间、候选状态机 | 单视频按 trackId 输出可解释的进入／离开／跨线候选 |
| P2 抽帧 dry-run | 事件窗口、真实帧配对、全景目标标记、证据预览 | 不加载 Qwen 即可人工核对“这个人、这些帧、这个 POI、这个时间” |
| P3 真实 Qwen 闭环 | 单候选单描述 → 多候选多描述；结构化校验及落盘 | CLI 对真实视频产生合法三态结果，含依据和可用的自报分数 |
| P4 Web 接入 | 上传、提交、轮询、取消、最小结果面板 | 用户从 Video 导入到查看结果，无需手工执行 CLI |
| P5 验收与复盘 | 七段现有视频、边界案例、性能与失败重试 | 验证报告区分工程闭环和语义效果，列出误判／漏触发实例 |

正式实施后按上述顺序持续推进，不要求用户每阶段再确认。若目标小、上下文不足或 JSON 不稳定，依据实际失败样本调整输入与提示；优先修复导致闭环不可用的问题。

## 11. 验证与验收

### 11.1 必要自动测试

- 几何与状态：有限线段／延长线、端点、死区、区域往返、边界抖动、区内初见、短漏检、长丢失、同 ID 新轨迹段；多人物同时触发不串人。
- 时间与采样：25／30 FPS、可变帧率、非零 PTS、帧框配对、窗口截断、32 帧上限、奇数 padding；模型时间 metadata 与原视频时间一致。
- 文本与结果：多描述独立结果、允许多命中、未知与否定分开、无效 JSON／NaN／错 ID／越界时间、token 截断、质量提示不覆盖模型判断、窗口不足不影响置信度、多片段阳性不丢失。
- 任务：mock worker 覆盖失败、部分成功、取消、服务重启后状态、重复提交、路径越界、上传失败；mock 通过不代替真实 GPU 验收。
- 回归：运行 Rules、backend 的相关 pytest；前端 test、lint、build；保留旧 Video、Rules 结果浏览及在线跟踪隔离行为。

### 11.2 真实视频验证

使用项目现有 `安检监控视频-测试/` 的 7 个视频，先选一段“未拦截”和一段“正常拦截”打通，再覆盖全部。逐机位核对 POI，d2 与 c2 不共用坐标，文件名只用于选择已核实的机位／组织样本，不作为业务真值或模型提示。

对每个候选保存并人工检查：

1. 人是否对应正确 ID，是否漏掉真实 POI 触发。
2. 抽帧中目标、门和潜在互动人员是否清晰，前后动作是否完整。
3. 模型是否区分“没拦”“尝试拦但没拦住”“成功制止”和“不清楚”。
4. 强行通过的描述是否有动作证据，不能把所有正常通过算成强闯。
5. 所有适用描述是否都有结果，失败项是否明确保留。

首版工程验收要求：页面闭环可运行；人物与候选可追溯；结果结构稳定；真实模型调用成功；无候选／未知／失败能区分；部分结果可保留并复查。**不以“模型必须把所有未拦截文件判为违规”作为通过条件。**

语义效果另报告人工复核一致性、uncertain 比例、候选漏触发和 ID 错配。准确率、精确率、召回率需事件级标注与明确分母；若尚无足够标注，报告逐例结果，不编造百分比。

性能记录包含：Pose 实际采样率、候选数、各描述调用数、Qwen 加载时间、每请求输入／输出 token、推理时间、解码时间、显存峰值、总耗时及失败数。原型约 4.57 GiB 的记录只作为起点，新增标注／分辨率／输出长度后重新实测。

## 12. 本次审阅所需确认的主要设计

建议采用：**复用现有 Rules 与 Qwen 环境；新增后端两阶段离线任务；POI 触发后按人物轨迹段和窗口抽帧；保留互动场景；自然语言描述可配置；三态输出并标明未校准的模型自报置信度；最终从 Video 页完成最小闭环。**

首版先使用“是否该人员强行通过安检门且无人阻拦”作为示例描述，后续文本合集可直接替换／增加，不需要重新开发分类器。几何阈值、前后窗口、采样率均是可调整初值，不作为已验证的业务标准。

本轮完成标准是本文档可供审阅。收到用户明确开始实施的指令后，再按 P1—P5 修改代码、运行真实模型并交付验证记录。
