import glob
import logging.handlers
import os
import time


class BoundedRotatingFileHandler(logging.handlers.RotatingFileHandler):
    def __init__(self, filename, max_bytes=10 * 1024 * 1024, backup_count=9, retention_days=7, **kwargs):
        os.makedirs(os.path.dirname(filename), exist_ok=True)
        self.retention_days = retention_days
        self.last_prune = 0
        super().__init__(filename, maxBytes=max_bytes, backupCount=backup_count, **kwargs)
        self._prune_expired()

    def emit(self, record):
        now = time.monotonic()
        if now - self.last_prune >= 3600:
            self._prune_expired()
        super().emit(record)

    def doRollover(self):
        super().doRollover()
        self._prune_expired()

    def _prune_expired(self):
        cutoff = time.time() - self.retention_days * 24 * 60 * 60
        for path in glob.glob(f"{self.baseFilename}.*"):
            try:
                if os.path.getmtime(path) < cutoff:
                    os.remove(path)
            except FileNotFoundError:
                pass
        self.last_prune = time.monotonic()
