"""Application-level retry for transient transport failures.

Needed because neither slack_sdk's default handlers nor requests' HTTPAdapter
retry the mid-body read errors we actually hit (IncompleteRead / SSL EOF).
This wraps the full call — including the body read — so the retry sees them.
"""
import ssl
import time
from http.client import IncompleteRead

import requests

TRANSPORT_ERRORS = (
    IncompleteRead,
    ssl.SSLError,
    requests.exceptions.ChunkedEncodingError,
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
)


def with_retries(fn, *, max_retries=4, base_delay=1.0, errors=TRANSPORT_ERRORS, sleep=time.sleep):
    """Call fn(); retry on transient transport errors with exponential backoff.

    Retries up to max_retries times (max_retries + 1 attempts total). Re-raises
    the last error if all attempts fail. `sleep` is injectable for tests.
    """
    attempt = 0
    while True:
        try:
            return fn()
        except errors:
            attempt += 1
            if attempt > max_retries:
                raise
            sleep(base_delay * (2 ** (attempt - 1)))
