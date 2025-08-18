# manual-aggregation-app/app/debug_utils.py
import sys

def d_print(message):
    """Отладочная печать с принудительным сбросом буфера."""
    print(message, flush=True, file=sys.stderr)