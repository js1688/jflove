# JFLove 官网镜像（多阶段构建）
#
# Stage 1: node 构建静态产物
# Stage 2: nginx 托管 dist
#
# 注意：官网**只暴露 :80**。TLS / 域名 / 反向代理由部署侧负责，
# 不写进镜像（见 website/docs/design-spec.md 与 AGENTS.md §10.7）。
#
# 构建前必须先跑内容管线，否则 dist 里会是旧内容：
#   python website/scripts/extract_sources.py
#   python website/scripts/gen_api_docs.py

FROM node:22-alpine AS builder
WORKDIR /app

# npm 国内镜像源（与 jflove-web 一致；可用 --build-arg 覆盖为官方源）
ARG NPM_REGISTRY=https://registry.npmmirror.com
RUN npm config set registry ${NPM_REGISTRY}

# 先只拷依赖清单，利用层缓存
COPY website/package.json website/package-lock.json* ./
RUN npm ci --no-audit --no-fund

# 再拷源码与内容数据（content/ 是构建期 import 进来的，必须一起拷）
COPY website/ ./

RUN npm run build

FROM nginx:1.27-alpine
COPY website/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/dist /usr/share/nginx/html

# 静态站点没有任何后端依赖，无需 VOLUME
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
