"""Load KEY=value lines from the project's .env. Values already in the environment win."""
import os
import re
from pathlib import Path

PATH = Path(__file__).resolve().parent / '.env'


def load(path=PATH):
    path = Path(path)
    if not path.exists(): return []
    loaded = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line: continue
        key, value = line.split('=', 1)
        key, value = key.strip().removeprefix('export ').strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in '"\'': value = value[1:-1]
        if re.fullmatch(r'[A-Z_][A-Z0-9_]*', key) and value and not os.environ.get(key, '').strip():
            os.environ[key] = value
            loaded.append(key)
    return loaded
