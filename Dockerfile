# 备选部署方式。主方案见 deploy/deploy.sh（不依赖 Docker Hub）。
# 前端已预构建在 web/dist，镜像内只需 Python（FastAPI + uvicorn）。
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PORT=8000 QUIET=1
EXPOSE 8000
VOLUME ["/app/state"]
CMD ["python", "-m", "server.app"]
