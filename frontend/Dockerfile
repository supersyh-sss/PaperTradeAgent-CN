# syntax=docker/dockerfile:1
# ============================================================
# PaperTradeAgent 前端镜像：多阶段构建
#   stage 1: node 编译产物   stage 2: nginx 托管 + /api 反向代理
# 构建：docker build -t papertrade-frontend ./frontend
# ============================================================

# ---- Build ----
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

# ---- Runtime ----
FROM nginx:1.27-alpine AS runtime
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
