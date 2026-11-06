# syntax=docker/dockerfile:1
#
# UltraConsole VLM 运行镜像。
#
# 设计约束（与交付方案一致）：
#   1. 镜像内不包含 CUDA/PyTorch 依赖与模型权重，两者体积大且需要独立升级；
#      首次启动由 setup 服务安装到挂载卷 /opt/ultra/envs，权重下载到 /models。
#   2. 前端在构建阶段编译为静态文件，运行期只跑 FastAPI（单端口 8001）。
#   3. 项目内路径自包含：/app/backend/models 由宿主机 ./models/detect 挂载，
#      Qwen 权重由 /models 挂载，二者都不进镜像。

ARG NODE_IMAGE=node:22-bookworm-slim
FROM ${NODE_IMAGE} AS web

WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./

# 鉴权 token 需要在构建期注入前端，否则页面请求不会带 Bearer 头。
# 默认留空 = 不启用鉴权（内网单机部署）。
ARG VITE_ULTRA_API_TOKEN=
ENV VITE_ULTRA_API_TOKEN=${VITE_ULTRA_API_TOKEN}
RUN npm run build


FROM python:3.10-slim-bookworm AS runtime

ARG ULTRA_VERSION=dev
LABEL org.opencontainers.image.title="UltraConsole VLM" \
      org.opencontainers.image.description="YOLO pose tracking + Qwen3-VL semantic verification" \
      org.opencontainers.image.version="${ULTRA_VERSION}" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUTF8=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_CACHE_DIR=/opt/ultra/pip-cache \
    HF_HUB_DISABLE_TELEMETRY=1 \
    HOME=/data/home \
    TZ=Asia/Shanghai

# libgl1/libglib2.0-0：opencv-python；libgomp1：PyTorch；tzdata：时区。
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      libgl1 libglib2.0-0 libgomp1 ca-certificates tzdata \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY backend /app/backend
COPY Rules /app/Rules
COPY --from=web /src/frontend/dist /app/frontend/dist
COPY deliverable/docker/server.py /app/server.py
COPY deliverable/docker /opt/ultra/bin
COPY deliverable/requirements /opt/ultra/requirements
COPY deliverable/VERSION /opt/ultra/VERSION
COPY LICENSE README.md /app/

RUN chmod +x /opt/ultra/bin/*.sh \
 && mkdir -p /opt/ultra/envs /opt/ultra/pip-cache /models /data

EXPOSE 8001
ENTRYPOINT ["/opt/ultra/bin/entrypoint.sh"]
CMD ["serve"]
