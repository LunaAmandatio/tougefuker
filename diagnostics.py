"""Bounded tool diagnostics. Never log RPC arguments or submitted source."""
import json
import logging
import re
import traceback
import uuid
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

BASE = Path(__file__).resolve().parent


def record_error(command, error):
    root = BASE / '.state'
    root.mkdir(parents=True, exist_ok=True)
    path = root / 'errors.log'
    logger = logging.getLogger('educoder-errors')
    if not logger.handlers:
        handler = RotatingFileHandler(path, maxBytes=300000, backupCount=2, encoding='utf-8')
        handler.setFormatter(logging.Formatter('%(message)s'))
        logger.addHandler(handler)
        logger.setLevel(logging.ERROR)
        logger.propagate = False
    error_id = uuid.uuid4().hex[:12]
    message = str(error)[:1500]
    message = re.sub(r'(?i)((?:cookie|authorization|token|sec_key)\s*[=:]\s*)[^\s,;]+', r'\1[redacted]', message)
    # Extract frame positions only, never Python locals, request payloads or source lines.
    frames = [{'file': Path(f.filename).name, 'line': f.lineno, 'function': f.name}
              for f in traceback.extract_tb(error.__traceback__)[-10:]]
    logger.error(json.dumps({'time': datetime.now(timezone.utc).isoformat(), 'id': error_id,
                 'operation': command, 'type': type(error).__name__,
                 'error': getattr(error, 'code', type(error).__name__), 'message': message,
                 'frames': frames}, ensure_ascii=False))
    return {'error_id': error_id, 'error_log': str(path)}


def read_logs(tail=30):
    tail = max(1, min(int(tail), 200))
    path = BASE / '.state' / 'errors.log'
    lines = path.read_text(encoding='utf-8').splitlines() if path.exists() else []
    return {'log_file': str(path), 'entries': [json.loads(line) for line in lines[-tail:]]}
