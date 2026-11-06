# WSL 新手运行指南

> 适用对象：Windows 11 + WSL2（Ubuntu），已经装好 WSL，但不太熟悉 Linux。
>
> 你不需要懂 Linux，只需要**按顺序复制命令、粘贴、回车**。首次安装会下载约 10GB 内容，请保持网络和终端窗口不要关闭。

---

## 开始前，确认 3 件事

1. **Windows 已安装 NVIDIA 显卡驱动**（NVIDIA App 或官网下载的 Game Ready 驱动都可以）。
2. **WSL 已经安装并能打开**（本指南不包含安装 WSL 的步骤）。
3. 电脑磁盘至少剩余 **30GB**，内存建议 **16GB** 以上。

---

## 第 0 步：打开 WSL 终端

在 Windows 开始菜单里搜索 **Ubuntu**，点击打开；或者在 PowerShell 里输入：

```powershell
wsl
```

打开后会看到一个黑色窗口，出现类似下面的提示符：

```text
你的用户名@电脑名:~$
```

这就是 Linux 终端。下面所有命令都**复制到 `$` 后面**，然后按回车。

> 提示：
> - 以 `#` 开头的是说明文字，不用输入。
> - 输入密码时屏幕不会显示任何字符，这是正常的，输完直接回车。

---

## 第 1 步：确认显卡在 WSL 里能用

在终端输入：

```bash
nvidia-smi
```

正常会显示一张表格，里面有显卡型号（例如 RTX 3080）和驱动版本。

如果提示 `command not found` 或报错：

1. 回到 Windows，用 PowerShell 执行：

   ```powershell
   wsl --shutdown
   ```

2. 重新打开 Ubuntu，再执行一次 `nvidia-smi`。
3. 仍然不行，就在 Windows 重新安装 NVIDIA 显卡驱动（Game Ready 即可）。

> **不要**在 WSL 里安装 NVIDIA 驱动。驱动只装在 Windows 侧。

---

## 第 2 步：下载项目

在终端里依次执行：

```bash
cd ~
git clone -b B3-feat/vlm-structured-verdict https://github.com/shydl3/ultraconsole-llm.git
cd ultraconsole-llm
```

> 如果提示 `Remote branch ... not found`，说明项目已经合并到默认分支，去掉 `-b B3-feat/vlm-structured-verdict` 再执行一次：
>
> ```bash
> git clone https://github.com/shydl3/ultraconsole-llm.git
> cd ultraconsole-llm
> ```

### 如果 `git clone` 失败（Connection reset / 连不上 GitHub）

如果你电脑上开着 Clash 类代理，并开启了「允许局域网连接 / Allow LAN」，可以先执行：

```bash
export http_proxy=http://$(ip route show default | awk '{print $3}'):7890
export https_proxy=$http_proxy
git clone -b B3-feat/vlm-structured-verdict https://github.com/shydl3/ultraconsole-llm.git
unset http_proxy https_proxy
cd ultraconsole-llm
```

如果不会配置代理，请让管理员把项目文件夹直接放到你的 WSL 家目录（`/home/你的用户名/`），然后：

```bash
cd ~/ultraconsole-llm
```

继续下一步即可。

---

## 第 3 步：一键安装并启动

在项目目录里执行：

```bash
bash scripts/bootstrap-wsl.sh
```

接下来脚本会自动完成：

- 安装 Miniconda（如果电脑上没有）
- 创建名为 `ultraconsole` 的 Python 环境
- 从国内镜像安装所有 Python 依赖
- 安装 Node.js（如果系统版本太低）
- 安装前端依赖
- 从 ModelScope 下载 Qwen 语义模型和 YOLO11 姿态模型
- 校验 CUDA 和模型
- 最后启动项目

中途会要求输入一次 **sudo 密码**（就是你的 WSL 登录密码），输入时屏幕不显示，输完回车即可。

安装过程中会看到大量下载和安装日志，**不要关闭窗口**。首次需要下载约 10GB，具体取决于网速。

---

## 第 4 步：打开页面

安装完成后，终端最后会显示类似：

```text
已启动：http://localhost:5100
```

脚本会尝试自动打开 Windows 浏览器；如果没有自动打开，手动在浏览器地址栏输入：

```text
http://localhost:5100
```

就能看到 UltraConsole 页面。

---

## 第 5 步：第一次使用

1. 在左侧上传一段监控视频。
2. 按页面提示在画面上画**入口计数线**；需要判断红灯时再画**信号区**。
3. 新建一条事件规则，并绑定到刚才画的线/区。
4. 点击「开始分析」。
5. 分析完成后，在「库」里查看结果和证据截图。

---

## 第 6 步：以后怎么启动、怎么停止

### 停止

在项目目录执行：

```bash
bash stop.sh
```

### 下次启动

重新打开 Ubuntu 终端，执行：

```bash
cd ~/ultraconsole-llm
source ~/.bashrc
conda activate ultraconsole
bash start.sh 5100
```

然后浏览器访问 `http://localhost:5100`。

> 如果提示 `conda: command not found`，执行：
>
> ```bash
> source ~/miniconda3/etc/profile.d/conda.sh
> conda activate ultraconsole
> ```

### 更新项目

```bash
cd ~/ultraconsole-llm
git pull
bash scripts/bootstrap-wsl.sh --no-start
bash start.sh 5100
```

`bootstrap-wsl.sh` 可以重复执行，已安装的内容会自动跳过。

---

## 常见问题

| 现象 | 处理 |
|---|---|
| `git clone` 提示 Connection reset | 用上面的代理命令，或让管理员拷贝项目文件夹 |
| `nvidia-smi: command not found` | 在 Windows 重装 NVIDIA 驱动，然后 `wsl --shutdown` 重开 |
| 提示 CUDA 不可用 | 在 Windows 执行 `wsl --shutdown`，重开 WSL 再启动 |
| `sudo: command not found` | 当前 WSL 用户没有管理员权限，请联系管理员 |
| 安装中途断了 | 重新执行 `bash scripts/bootstrap-wsl.sh`，已完成部分会跳过 |
| 端口 5100 被占用 | 改用 `bash scripts/bootstrap-wsl.sh --port 8080`，之后访问 `http://localhost:8080` |
| 浏览器打不开 | 手动访问 `http://127.0.0.1:5100` |
| 提示磁盘空间不足 | 至少预留 30GB，清理后重试 |
| 页面能开但分析报错 | 先确认 `nvidia-smi` 和 `torch.cuda` 正常，再看终端里的错误 |

---

## 请不要做这些事

- **不要**在 WSL 里安装 NVIDIA 驱动（`nvidia-driver-*`、`cuda-drivers`）。
- **不要**用 `sudo` 运行整个安装脚本；只有脚本里的系统包安装会提示 sudo。
- **不要**在首次安装过程中关闭终端或关机。
- **不要**手动删除 `models/`、`Rules/data/vlm/`，里面是模型和分析记录。

---

## 文件都在哪里

| 路径 | 内容 |
|---|---|
| `~/ultraconsole-llm` | 项目代码 |
| `models/` | Qwen 语义模型 |
| `backend/models/` | YOLO11 姿态模型 |
| `Rules/data/vlm/` | 上传的视频、分析记录、证据截图 |
| `tmp/ultraconsole/` | 前后端运行日志 |

需要停止服务时，执行 `bash stop.sh`；需要重启时，按第 6 步操作。
