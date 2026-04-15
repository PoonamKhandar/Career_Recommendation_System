# gunicorn.conf.py — Render deployment config
import os

bind = f"0.0.0.0:{os.environ.get('PORT', '10000')}"
workers = 1        # 1 worker on free tier to save memory
timeout = 120      # 120s timeout — needed for OpenAI API calls (default 30s too short)
worker_class = "sync"
preload_app = False
loglevel = "info"
accesslog = "-"
errorlog = "-"
