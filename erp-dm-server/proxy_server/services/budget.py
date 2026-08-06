"""Shared deterministic Stage 2B context estimate."""

import math


def approximate_token_count(text: str) -> int:
    return math.ceil(len(text) / 4)
