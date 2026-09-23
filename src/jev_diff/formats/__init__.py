"""Source formats: each turns one order-guide file into the canonical model."""

from .base import FormatAdapter, UnsupportedFormat
from .registry import adapter_for, adapters, detect_format

__all__ = ["FormatAdapter", "UnsupportedFormat", "adapter_for", "adapters", "detect_format"]
