"""Tokenization shared by the sparse encoder, chunkers and query analysis."""

from __future__ import annotations

import re

# Identifiers that must match exactly: register/macro names (TIM2_CCR1, HAL_I2C_Master_Transmit), hex
# literals (0x40021000) and part numbers (STM32F407VG, nRF52840, ATmega328P, ESP32-S3).
_IDENTIFIER = re.compile(
    r"\b(?:0x[0-9a-fA-F]+|[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+|[A-Z][A-Z0-9]{2,}[0-9][A-Z0-9]*|"
    r"(?:STM32|nRF|ESP32|ATmega|ATtiny|PIC|MSP430|RP2040|LPC|SAMD)[\w-]*)\b"
)
_WORD = re.compile(r"[A-Za-z0-9]+(?:[._-][A-Za-z0-9]+)*")


def approx_tokens(text: str) -> int:
    """Cheap token estimate (about 0.75 words per token for English prose and code)."""
    return max(1, round(len(_WORD.findall(text)) * 1.3))


def identifiers(text: str) -> list[str]:
    return list(dict.fromkeys(m.group(0) for m in _IDENTIFIER.finditer(text)))


def words(text: str) -> list[str]:
    return _WORD.findall(text)
