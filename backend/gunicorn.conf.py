# ============================================================
# 文件：backend/gunicorn.conf.py
# 说明：Gunicorn 生产配置。
# 依据：补充裁定（一）→ 1 worker + 内存广播 SSE
# ============================================================

import multiprocessing  # noqa: F401  （保留以备未来调整）

# ---------- 监听 ----------
# 只监听回环地址：外部一律经 nginx 反代访问，不把 API 端口直接暴露到公网
bind = "127.0.0.1:8000"
backlog = 128

# ---------- Worker（补充裁定：1 worker）----------
workers = 1
worker_class = "uvicorn.workers.UvicornWorker"
worker_tmp_dir = "/dev/shm"    # 使用内存临时目录，减少磁盘 IO

# ---------- 超时（SSE 长连接）----------
timeout = 300                  # 补充裁定：120 → 300
graceful_timeout = 30
keepalive = 60

# ---------- 请求上限（防内存泄漏）----------
max_requests = 2000
max_requests_jitter = 100

# ---------- 日志 ----------
accesslog = "logs/access.log"
errorlog = "logs/error.log"
loglevel = "info"
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)sμs'

# ---------- 其他 ----------
proc_name = "multi-ai"
preload_app = False            # 不预加载，避免 fork 后连接池污染
