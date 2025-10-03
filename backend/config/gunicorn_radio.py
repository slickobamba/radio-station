import multiprocessing

# Server socket
bind = "127.0.0.1:8001"
backlog = 2048

# Worker processes
workers = 2
worker_class = "uvicorn.workers.UvicornWorker"
worker_connections = 1000
timeout = 120
keepalive = 5

# Logging
accesslog = "/home/dirk/backend/logs/radio_access.log"
errorlog = "/home/dirk/backend/logs/radio_error.log"
loglevel = "info"
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s"'

# Process naming
proc_name = "radio"

# Server mechanics
daemon = False
pidfile = "/home/dirk/backend/logs/radio.pid"
umask = 0o022
user = None
group = None
tmp_upload_dir = None