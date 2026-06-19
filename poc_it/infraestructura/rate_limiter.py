"""
Rate limiting y resiliencia para proveedores LLM.

Incluye:
- Exponential Backoff ante 429
- Lectura de headers de rate limit
- Token bucket simple en memoria (TPM)
"""

from __future__ import annotations

import time
import threading
from typing import Optional


# ==========================================================
# CONFIGURACIÓN BÁSICA (ajustable por plan)
# ==========================================================

DEFAULT_TPM_LIMIT = 90000 # Ajustar
BACKOFF_BASE = 1.5
BACKOFF_MAX_SECONDS = 30
MAX_RETRIES = 5


# ==========================================================
# TOKEN BUCKET (ventana deslizante simple)
# ==========================================================

class TokenBucket:
    def __init__(self, tokens_per_minute: int):
        self.capacity = tokens_per_minute
        self.tokens = tokens_per_minute
        self.last_refill = time.time()
        self.lock = threading.Lock()

    def _refill(self):
        now = time.time()
        elapsed = now - self.last_refill
        refill_amount = (self.capacity / 60.0) * elapsed
        self.tokens = min(self.capacity, self.tokens + refill_amount)
        self.last_refill = now

    def consume(self, amount: int):
        with self.lock:
            self._refill()
            if amount > self.tokens:
                wait_time = (amount - self.tokens) / (self.capacity / 60.0)
                time.sleep(wait_time)
                self._refill()
            self.tokens -= amount


# Instancia global
GLOBAL_BUCKET = TokenBucket(DEFAULT_TPM_LIMIT)


# ==========================================================
# EXPONENTIAL BACKOFF
# ==========================================================

def exponential_backoff_sleep(attempt: int):
    delay = min(BACKOFF_BASE ** attempt, BACKOFF_MAX_SECONDS)
    time.sleep(delay)


# ==========================================================
# HEADER RATE LIMIT HANDLER
# ==========================================================

def handle_rate_limit_headers(response):
    """
    Lee headers estándar si existen.
    Compatible con Groq/OpenRouter si exponen:
    - x-ratelimit-remaining-tokens
    - x-ratelimit-reset
    """

    remaining = response.headers.get("x-ratelimit-remaining-tokens")
    reset = response.headers.get("x-ratelimit-reset")

    if remaining is not None:
        try:
            remaining = int(remaining)
            if remaining < 100:
                time.sleep(1)
        except ValueError:
            pass

    if reset is not None:
        try:
            reset_seconds = float(reset)
            time.sleep(reset_seconds)
        except ValueError:
            pass
