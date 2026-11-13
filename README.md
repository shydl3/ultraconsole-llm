<div align="center">

# UltraConsole

### AI 监控视频事件分析控制台

YOLO 姿态跟踪 + 事件规则判定 + Qwen3-VL 视觉大模型语义分析

</div>

<p align="center">
  <img src="runtime1.png" alt="UltraConsole 预览" width="80%" />
</p>

UltraConsole 用 GPU 分析监控视频：先由 YOLO11m-Pose 跟踪人物轨迹找出**候选事件时间点**，再结合传统 CV 信号（红灯检测、跨线、退回）与 **Qwen3-VL 视觉大模型**判定**事件是否真的发生**，输出带证据、可回放、可人工复核的结果。

本文档面向**不熟悉 Linux 的用户**，跟着复制粘贴命令即可完成部署。

---

## 一、部署前准备

在开始之前，请确认你的电脑满足以下条件：

| 条件 | 说明 |
|---|---|
| 操作系统 | Windows WSL2 |
| 显卡 | NVIDIA 显卡，已安装官方驱动（模型推理需要 CUDA 加速） |
| 磁盘空间 | 至少 **40 GB** 空闲（Python 依赖约 8 GB + 模型约 13 GB（2B+4B）+ 视频数据） |
| 网络 | 可访问国内镜像源（脚本默认全部走清华 / npmmirror / ModelScope 镜像） |

> **Windows 用户**：请先安装 [WSL2](https://learn.microsoft.com/zh-cn/windows/wsl/install)（在 PowerShell 管理员模式执行 `wsl --install`），并在 Windows 侧安装 [NVIDIA 显卡驱动](https://www.nvidia.cn/drivers/)。之后所有命令都在 WSL2 的 Ubuntu 终端里执行。（注意：Windows只要安装了nvidia显卡驱动，一般就代表ok了）
>
> **验证显卡是否可用**：打开powershell，在终端输入下面这条命令，能看到显卡信息输出即为正常：
>
> ```bash
> nvidia-smi
> ```

---

## 二、获取项目代码

如果还没有项目代码，先用 git 克隆到本地（`git clone` 就是把远程仓库完整下载到你的电脑）：

```bash
git clone <你的仓库地址> ultraconsole
cd ultraconsole
```

已经有代码的用户，直接进入项目目录即可：

```bash
cd ultraconsole
```

> 后面所有命令都默认在 `ultraconsole` 项目根目录下执行。

---

## 三、一键部署（推荐）

项目提供了 `deploy.sh` 部署脚本，**一条命令自动完成全部安装**：

```bash
bash deploy.sh
```

脚本执行过程中会提示你输入一次 **sudo 密码**（即开机/管理员密码，输入时屏幕不显示任何字符是正常现象，输完按回车即可）。

脚本会依次自动完成以下 9 个步骤（每步都已在电脑上完成过的会自动跳过，因此**中断后可以放心重新执行**）：

| 步骤 | 内容 | 作用 |
|---|---|---|
| 1 | 安装系统依赖 | curl（下载工具）、git、libgl1 等 OpenCV 运行所需的图形库 |
| 2 | 安装 Miniconda | Python 环境管理器，用于创建与系统隔离的 Python 环境 |
| 3 | 创建 `ultraconsole` 环境并安装 Python 依赖 | Python 3.10 + PyTorch + FastAPI + Qwen 推理库（首次约 6-8 GB，耗时最长） |
| 4 | 安装 Node.js 22 | 前端页面的运行环境 |
| 5 | 安装前端依赖 | `npm ci` 按锁定文件精确安装 React/Vite 等前端包 |
| 6 | 下载模型权重 | YOLO11m-Pose（约 40 MB）+ Qwen3-VL-2B（约 4 GB）+ Qwen3-VL-4B（约 9 GB），均来自 ModelScope |
| 7 | 写入 `backend/.env` | 告诉后端用哪个 Python 来跑大模型推理 |
| 8 | 校验安装 | 检查 PyTorch / CUDA / 模型文件是否全部就绪 |
| 9 | 启动服务 | 调用 `./start.sh` 启动前后端 |

看到绿色的 **`部署完成，服务已启动：http://localhost:5100`** 即表示成功，用浏览器打开该地址即可使用。

### 常用参数

```bash
bash deploy.sh --port 8080     # 换用 8080 端口启动前端（默认 5100）
bash deploy.sh --no-start      # 只安装部署，不自动启动
bash deploy.sh --skip-apt      # 跳过系统包安装（没有 sudo 权限时用）
bash deploy.sh --yolo /path/to/yolo11m-pose.pt   # 使用已下载好的 YOLO 权重文件
```

---

## 四、手动分步部署（可选）

如果你希望了解每一步在做什么，或者一键脚本在你的环境上遇到问题，可以按下面的步骤逐条执行。

### 1. 安装系统依赖

```bash
sudo apt-get update
sudo apt-get install -y curl ca-certificates xz-utils git libgl1 libgomp1 libglib2.0-0t64
```

- `apt-get update`：刷新软件源列表，让系统知道有哪些软件可装。
- `libgl1` / `libglib2.0-0t64`：OpenCV（图像处理库）运行必需的系统图形库，缺了会报 `import cv2` 错误。

### 2. 安装 Miniconda（已有 conda 可跳过）

```bash
curl -fL -o /tmp/miniconda.sh https://mirrors.tuna.tsinghua.edu.cn/anaconda/miniconda/Miniconda3-latest-Linux-x86_64.sh
bash /tmp/miniconda.sh -b -p $HOME/miniconda3
source $HOME/miniconda3/etc/profile.d/conda.sh
```

- 第一条：从清华镜像下载 Miniconda 安装包。
- 第二条：`-b` 表示静默安装到 `$HOME/miniconda3` 目录。
- 第三条：让当前终端立刻认识 `conda` 命令。

### 3. 创建 Python 环境并安装依赖

```bash
conda create -n ultraconsole python=3.10 -y
conda activate ultraconsole
pip install -r backend/requirements-wsl.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

- `conda create`：创建一个名为 `ultraconsole` 的独立 Python 3.10 环境，不会污染系统 Python。
- `conda activate`：切换到这个环境（之后终端提示符前会出现 `(ultraconsole)` 字样）。
- `pip install -r`：按依赖清单安装 PyTorch、FastAPI、Qwen 推理库等；`-i` 指定清华镜像加速下载。

### 4. 安装 Node.js（已有 20+ 版本可跳过）

```bash
curl -fL -o /tmp/node.tar.xz https://npmmirror.com/mirrors/node/v22.14.0/node-v22.14.0-linux-x64.tar.xz
mkdir -p $HOME/.local/node
tar -xJf /tmp/node.tar.xz -C $HOME/.local/node --strip-components=1
export PATH="$HOME/.local/node/bin:$PATH"
echo 'export PATH="$HOME/.local/node/bin:$PATH"' >> $HOME/.bashrc
```

- 从 npmmirror 镜像下载 Node.js 22 压缩包并解压到用户目录（不需要 sudo）。
- 最后一条把路径写入 `~/.bashrc`，让以后每次打开终端都能使用 `node` / `npm` 命令。

### 5. 安装前端依赖

```bash
cd frontend
npm ci --registry=https://registry.npmmirror.com
cd ..
```

- `npm ci`：严格按照 `package-lock.json` 安装前端依赖，保证每个人装出来的版本完全一致。

### 6. 下载模型权重

```bash
# YOLO11m-Pose：人物姿态跟踪模型（约 40 MB）
mkdir -p backend/models
curl -fL -o backend/models/yolo11m-pose.pt \
  https://modelscope.cn/models/Ultralytics/YOLO11/resolve/master/yolo11m-pose.pt

# Qwen3-VL-2B + 4B：视觉语言大模型（约 4 GB + 9 GB，脚本自动校验完整性、支持断点续传）
MODELS_DIR=models VLM_MODEL_REPO=Qwen/Qwen3-VL-2B-Instruct \
VLM_MODEL_PATH=models/Qwen3-VL-2B-Instruct \
YOLO_PATH=backend/models/yolo11m-pose.pt VLM_MODEL_SOURCE=modelscope \
HF_ENDPOINT=https://hf-mirror.com \
python scripts/download_models.py

MODELS_DIR=models VLM_MODEL_REPO=Qwen/Qwen3-VL-4B-Instruct \
VLM_MODEL_PATH=models/Qwen3-VL-4B-Instruct \
YOLO_PATH=backend/models/yolo11m-pose.pt VLM_MODEL_SOURCE=modelscope \
HF_ENDPOINT=https://hf-mirror.com \
python scripts/download_models.py
```

| 模型 | 用途 | 存放位置 |
|---|---|---|
| `yolo11m-pose.pt` | 人员检测与姿态跟踪 | `backend/models/` |
| `Qwen3-VL-2B-Instruct` / `4B-Instruct` | 事件语义判定（VLM，2B 为默认） | `models/` |

### 7. 写入后端配置

```bash
echo "VLM_PYTHON=$(which python)" >> backend/.env
```

- 把当前 conda 环境的 Python 路径写入 `backend/.env`，后端会用它来启动 Qwen 大模型推理。
- 前提是当前处于 `(ultraconsole)` 环境中（`which python` 才能查到正确路径）。

### 8. 启动

```bash
./start.sh
```

看到 `访问地址：http://localhost:5100` 后，用浏览器打开即可。

---

## 五、日常启动 / 停止

部署完成后，以后每次使用只需要两条命令：

```bash
./start.sh          # 启动（前端默认 5100 端口，后端固定 8001）
./stop.sh           # 停止全部服务
```

```bash
./start.sh 8080     # 指定前端端口启动
./start.sh --prod   # 生产模式：构建前端并由后端 8001 直接托管（页面与视频同源，无 Vite 代理）
```

- 日志目录：`tmp/ultraconsole/`（`backend.log` 后端日志、`frontend.log` 前端日志）。
- 后端启动时会自动选择 CUDA 并加载姿态模型，无需手动配置。
- **开发模式**（默认）由 Vite 提供页面，`/api` 经开发代理转发到后端；**生产模式**只需访问 `http://localhost:8001`，页面、接口、视频字节范围请求都在同一来源，视频拖动更稳，适合日常使用。

---

## 六、使用流程

1. 打开浏览器访问 **http://localhost:5100**。
2. 上传监控视频。
3. 在画面上绘制入口计数线，按需绘制信号区（红灯）或计数区。
4. 定义事件规则，并绑定到对应的 POI / 信号区。
5. 点击「开始」分析，结果（结论 + 原因 + 证据帧）会出现在记录库中，可点击回放复核。

---

## 七、常见问题

**Q1：提示 `command not found: conda`？**
conda 尚未初始化到当前终端。执行 `source ~/.bashrc`，或关闭终端重新打开。

**Q2：校验时显示 `cuda: False`？**
显卡驱动未生效。WSL2 用户请在 Windows PowerShell（管理员）执行 `wsl --shutdown`，然后重新打开 WSL 终端再运行 `bash deploy.sh`（已完成的步骤会自动跳过）。仍不行请重装 [NVIDIA 驱动](https://www.nvidia.cn/drivers/)。

**Q3：模型下载太慢或失败？**
脚本默认走 ModelScope（国内源）。如果仍失败，可手动从 [ModelScope Qwen3-VL-2B-Instruct](https://modelscope.cn/models/Qwen/Qwen3-VL-2B-Instruct) 页面下载全部文件，放到 `models/Qwen3-VL-2B-Instruct/` 目录后重新执行 `bash deploy.sh`。

**Q4：端口被占用（`端口 5100 已被其他进程占用`）？**
换一个端口启动：`./start.sh 8080`。

**Q5：`import cv2` 报错 `libGL.so.1: cannot open shared object file`？**
缺少系统图形库，执行：`sudo apt-get install -y libgl1 libglib2.0-0t64`。

**Q6：部署脚本中途报错退出了怎么办？**
根据错误提示解决后，直接重新执行 `bash deploy.sh` 即可——脚本是幂等的，已完成的步骤会自动跳过。

---

## 八、进阶配置（可选）

以下环境变量写入 `backend/.env` 即可生效，普通用户无需修改：

| 变量 | 默认值 | 作用 |
|---|---|---|
| `VLM_MODEL_PATH` | `models/Qwen3-VL-2B-Instruct` | Qwen3-VL 模型目录 |
| `VLM_PYTHON` | 当前 Python | VLM 推理解释器（独立环境时覆盖） |
| `VLM_DATA_ROOT` | `Rules/data/vlm` | 分析数据根目录 |
| `ULTRA_API_TOKEN` | *(空)* | HTTP 鉴权令牌（设置后接口需要携带令牌） |
| `ULTRA_DEVICE` | *(自动)* | 强制指定 `cpu` / `cuda` |
| `ULTRA_AUTOSTART` | `true` | 启动时自动选设备并加载模型 |
| `ULTRA_DEFAULT_MODEL` | `yolo11m-pose.pt` | 默认检测模型文件名 |
| `VLM_SEGMENT_SECONDS` | `600` | 视频分段时长（秒） |
| `VLM_MAX_UPLOAD_BYTES` / `VLM_MAX_DURATION_SEC` | `128 GiB` / `172800` | 单条视频的上传大小 / 时长上限（`0` 表示不限制，12–24h 长录像默认可分析） |
| `VLM_PLAYBACK_MAX_DURATION_SEC` | `43200` | 生成回放代理的时长上限（`0` 表示不限制；超长录像直接播放原片） |

开发相关文档见 [dev-guide.md](dev-guide.md)，WSL 详细说明见 [WSL 新手运行指南](docs/WSL-新手运行指南.md)。

---

## 九、界面总览

打开 **http://localhost:5100** 进入**控制台**。整个应用有两个视图，可随时切换：

- **控制台**：三栏工作台，用于上传视频、绘制规则、运行分析（默认视图）。
- **记录与视频管理**：查看 / 回放已保存的分析结果与转换视频。点顶部栏左侧「记录与视频管理」进入，页内再点「打开控制台」返回。

控制台布局（三栏宽度可拖拽调整，浏览器会记住你的布局）：

```
┌──────────────────────────────────────────────────────────────┐
│ 顶部栏： 记录与视频管理 · Model · Device(CPU/CUDA) ‖ FPS · 中/EN │
├────────────┬───────────────────────────────────┬──────────────┤
│ 左栏·控制    │ 中栏·Video 卡片                     │ 右栏·监控      │
│ Model Hub   │ ① 工具栏：选择视频 / 从头分析 / 导出CSV │ 系统资源       │
│ Engine      │    画计数线 / 进入方向 / 画计数区       │ 配置导入导出   │
│ NMS 阈值     │    画信号区 / 清空线区 · FPS · ROI …   │ 计数 / 事件    │
│ 类别过滤     │ ② 视频画面 + 检测框 / 轨迹叠加          │ 告警引擎       │
│ 跟踪开关     │ ③ POI 视频语义分析（VLM）面板           │ 事件日志       │
└────────────┴───────────────────────────────────┴──────────────┘
```

顶部栏右侧「中/EN」可一键切换中英文界面；`Model` / `Device` 显示当前模型与推理设备，`FPS` 显示实时处理帧率。

---

## 十、两种分析模式（先理解这个）

UltraConsole 有两条并行的分析路径，都从中间的 Video 卡片开始：

| 模式 | 回答的问题 | 用到的能力 | 结果去向 |
|---|---|---|---|
| **A｜经典 CV 检测 / 计数** | 画面里有什么、跨了几次线、进了几次区 | YOLO11-Pose + ByteTrack | 右栏「计数 / 事件」「告警」「日志」，可导出 CSV |
| **B｜VLM 语义事件分析（核心）** | 这条告警到底成不成立、证据在哪一帧 | 确定性信号（红灯 / 跨线 / 退回）+ Qwen3-VL 逐条件判定 | 中栏 VLM 面板结果列表（带证据帧），并保存到「记录与视频管理」 |

> 一句话：模式 A 负责"看见"，模式 B 负责"判断是否真的发生"。两者可独立使用，也可先用 A 画好线 / 区，再用 B 做事件核验。

---

## 十一、模式 A：经典 CV 控制台使用手册

1. **确认模型与引擎**（左栏）
   - `Model Hub`（模型选择）：上半部分是检测模型，启动时已自动加载 `yolo11m-pose.pt`；下半部分是语义分析模型（Qwen3-VL 2B / 4B），选择后对下一次语义分析任务生效。
   - `Engine`（推理引擎）：`CPU` / `GPU (CUDA)`。启动会自动选 CUDA，一般无需手动改。
   - `NMS Controls`：`Conf Threshold`（置信度阈值，越高误检越少）、`IoU Threshold`（检测框去重阈值）。
   - `Class Filter`（分类标签）：选模型后出现类别标签，点击可只保留关心的类别。
   - `Tracking & Events`（目标跟踪）：跟踪开关（视频分析会自动启用 ByteTrack）。

2. **载入视频**（中栏工具栏）
   - 点「选择视频」，或直接把视频文件拖进画面区域。支持 mp4 / mov / mkv / avi / m4v / webm / ts 等常见格式。
   - 浏览器无法直接播放的格式会自动生成 H.264 兼容预览（长录像可能需要几分钟，可点「停止」取消）。

3. **绘制计数线 / 计数区 / 信号区**
   - 「画计数线」：在画面点两个位置画一条线，再点「完成绘制」。用于跨线计数。
   - 「进入方向」：设置计数线正向（→ / ←）或「双向」；单向时反向跨越会被视为"退回"。
   - 「画计数区」：依次点多边形顶点（≥3 个），点起点闭合。用于区域进出 / 滞留计数。
   - 「画信号区」：框选门框红灯灯带所在区域（≥3 个点闭合），供模式 B 的红灯检测使用。
   - 「清空线 / 区」：清除当前绘制的所有线、区、信号区。

4. **ROI 与画面叠加**（可选）
   - `ROI` + `矩形 / 多边形`：限定只关注某块区域；勾选「仅显示 ROI 内」可过滤 ROI 外的检测框。
   - `BBox` / `Labels` / `热力图`：控制检测框、类别标签、热力图的显示。

5. **运行分析**
   - 设 `FPS`（采样帧率，1–30，默认 8），点「从头分析（含跟踪）」。
   - 分析中显示进度（已分析时长 / 帧数 / 事件数），可随时「停止」（保留部分结果）。
   - 修改模型、引擎、参数或规则会自动中止本次分析，避免结果混淆。

6. **查看结果**（右栏）
   - `事件计数`：计数线的 in / out、计数区的 now / total，以及最近事件流水；「重置」可清零。
   - `告警规则`：勾选「启用」，选目标类别 + 连续帧数 N，命中即告警（可开声音）。**注意：录像分析不触发实时告警**，告警规则用于实时画面源。
   - `事件日志`：操作与事件流水，点「导出 CSV」下载。
   - `配置文件`（导入 / 导出）：把模型参数、引擎、OSD、ROI、计数线区、告警、Webhook 等配置导出为 JSON，或拖入 JSON 一键还原。

7. 点「导出分析 CSV」下载本次视频分析的报表。

---

## 十二、模式 B：VLM 语义事件分析使用手册（核心）

这是 UltraConsole 的重点：在 POI 触发点上判断"事件是否真的发生"，并给出可回放的证据。面板位于中栏 Video 卡片底部，标题「POI 视频语义分析」。

> **前置条件**：先选择视频，并至少画一条计数线或计数区作为 POI（否则面板提示"请先选择视频并绘制 POI"）。

### 12.1 完整流程

1. 选择视频 → 画计数线 / 计数区（POI）；如需红灯判定，再画「信号区」。
2. 在「事件规则定义」新增 / 导入规则，填写事件描述与判断条件，绑定 POI（和信号区），勾选「启用」。
3. （可选）在「信号区阈值」填红灯校准值。
4. 在左侧「Model Hub」（模型选择）选语义分析模型，在「分析参数」调抽帧频率等；也可先勾「仅抽帧预览」干跑一次，确认抽帧无误再正式调用大模型。
5. 点「开始语义分析」（若有未保存的规则改动，按钮显示「保存规则并开始分析」）。
6. 后台完成后，在结果列表查看每条结论与证据帧。

### 12.2 事件规则定义

- 顶部可「新增规则」「批量添加」（每行一条事件描述）「导入 JSON / TXT」「导出」「重新加载」。
- 每条规则包含：
  - **规则名称** + **事件描述**（这段描述会作为"判断问题"发给模型）。
  - **触发类型**：跨线 / 进入区域 / 离开区域 / 起始已在区域内——只有匹配触发类型且绑定到对应 POI 的动作才会成为候选。
  - **本次 POI / 信号区**：把规则绑定到画好的线 / 区 / 信号区（POI 绑定仅对本次分析生效）。
  - **事件前采样(秒) / 事件后采样(秒)**：以触发点为中心截取的证据窗口（默认前 4 秒、后 6 秒）。
  - **判断条件**（每行一条，可选）：把"事件成立"拆成若干可逐条核验的条件；模型需逐条给「满足 / 不满足 / 无法确认」+ 证据时间，代码再按合取规则合并出最终结论。
  - **无法判断的情况**（每行一条，可选）：告诉模型哪些情形应判为"证据不足"。
- 「启用」开关决定该规则是否参与分析，单次分析最多启用 **30 条**。修改先存为草稿，点「保存全部修改 / 保存所选」后才持久生效。

### 12.3 信号区阈值（红灯检测校准）

用于确定性层的红灯判定。字段：`红灯占比阈值`、`R 最小`、`R−G 最小`、`R−B 最小`、`亮起持续(秒)`、`熄灭持续(秒)`、`采样帧率`。建议用校准工具测出建议值后填写；清空某字段即回退默认值。

### 12.4 分析参数

| 参数 | 含义 |
|---|---|
| 语义分析模型 | 在左栏「Model Hub」下半部分选择本机 Qwen3-VL 权重（2B / 4B）；4B 更准但更吃显存，下次任务生效 |
| Qwen 抽帧频率(帧/秒) | 事件窗口内的抽样频率，另保留触发附近与首尾帧 |
| 每段最大输入帧数 | 必须为偶数；越大越细，但更慢、更吃显存 |
| 单帧像素预算 | 196608 为标准 / 393216 为较高细节 |
| 姿态检测频率(帧/秒) | 用于人物跟踪与 POI 候选检测，也决定可用观测帧密度 |
| 模型输出上限(token) | 详细描述 + 判断依据建议 ≥ 1024，过低会截断回答 |
| 姿态检测设备 | 默认 CUDA（仅控制姿态检测，Qwen 始终用 GPU） |
| 人物检测置信度阈值 / 检测框去重 IoU | 控制 YOLO 的检测筛选与去重 |
| POI 状态确认时长(秒) / POI 边界缓冲(像素) | 防抖：状态持续达标才确认；缓冲减少边界反复触发 |
| 跟踪最大间断(秒) | 超过此间隔则新建轨迹段 |
| 仅抽帧预览，不调用 Qwen | 干跑（dry run）：只看抽了哪些帧，不消耗大模型 |

设置会自动保存在当前浏览器、提交新任务时生效；「恢复默认参数」可一键还原；「查看本次任务实际参数」可回看某次任务的参数快照。

### 12.5 读懂结果

每条结果对应一个 (跟踪 ID · 触发时间 · POI) 的判定：

- **结论**：`匹配`（事件成立）/ `不匹配`（未发生）/ `证据不足`（看不清就不算发生）。
- **确定性判定**：由传统 CV 直接算出的结论（红灯状态、退回、轨迹连续性）；`红灯检测` 会显示亮 / 灭状态与起始时间。
- **逐条件证据**：每个判断条件的 `满足 / 不满足 / 无法确认`，点「证据 @xx.xx s」可跳到对应帧。
- **视频内容描述 / 判断依据**：模型对该片段的客观描述与推理理由。
- **模型原始判断 / 置信度**：模型自述结论与置信度（未校准，非违规概率）；当逐条件合取结论与模型自述不一致时以合取为准，并在此标注。
- **辅助信息 / 需复核原因**：不改变判断的提示（目标太小、帧太少、身份歧义、证据时间落在窗口外等），以及被降级为"证据不足"的原因。
- 点结果标题可展开分段详情并显示**证据帧**缩略图；每条结果都有「跳转到此处」按钮，播放器会停在触发点前（预跳秒数可调），也可用「上一处 / 下一处」和时间轴刻度在事件之间移动。

「任务记录」下拉可切换查看历史任务，运行中的任务可「取消」；点「查看记录」打开记录查看窗口，左侧保留视频回放与事件时间轴，右侧按判定类型（匹配 / 不匹配 / 证据不足 / 执行失败 / 干跑 / 需复核）、事件规则、轨迹筛选，并显示各项数量聚合。任务状态：排队中 / 姿态跟踪与 POI 检测 / Qwen 语义判断 / 已完成 / 部分完成 / 执行失败 / 已取消。

大文件（1 GB 以上的监控原片）首次复核时，可点视频下方的「生成回放代理」：用 GPU 一次性转成 faststart + 1 秒关键帧的副本（保持 1080p，码率上限 6 Mbps），之后拖动、跳转几乎即时；原视频与分析结果不受影响，副本按 videoId 缓存。

---

## 十三、记录与视频管理

点顶部栏「记录与视频管理」进入。这里统一保存语义分析记录与转换视频：

- **筛选 / 搜索**：按「全部记录 / 语义分析 / 转换视频」筛选，或搜索名称、原文件名、记录 ID。
- **列表字段**：名称、状态（已就绪 / 运行中 / 未知）、创建时间、处理时长、结果 / 大小、操作。
- **查看**：打开「记录查看窗口」，可回放源视频、按判定类型 / 事件规则 / 轨迹筛选并计数分析结果、逐条「跳转到此处」复核证据，也支持下载视频与查看原始记录信息；大文件可「生成回放代理」以获得顺滑拖动。
- **重命名**：只改显示名，保留原文件名、记录 ID 与视频复用关系。
- **删除**：勾选后「删除所选」，**删除不可撤销**；运行中的分析需先在控制台停止才能删。共享的原视频会保留，转换视频会从保存目录移除。

---

## 十四、使用小贴士

- 三栏宽度、右栏下沉高度都可拖拽调整，布局会记在浏览器本地。
- 每次「从头分析」都会清空上次统计重新开始；低采样率可能漏检，关键事件建议提高 FPS。
- 想省显存或先验证画线是否正确，先勾「仅抽帧预览」干跑一次，再正式调用 Qwen。
- 换 4B 模型前留意显存占用；不确定就先「恢复默认参数」。
- 目前准确率较高的可核验事件：人员进出选定区域安检报警、物品推车进出选定区域检测动作；更多规则可持续在「事件规则定义」中扩展。

---

## License

[MIT](LICENSE)
