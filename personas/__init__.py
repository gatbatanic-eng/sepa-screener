"""추세 통과 종목에 대한 페르소나별 판단 논리(규칙 기반 증거 패킷)."""
from personas.logic import (
    DISCLAIMER,
    FORBIDDEN_PHRASES,
    PERSONAS,
    THRESHOLDS,
    build_evidence,
    format_text,
    normalize_code,
)

__all__ = ["build_evidence", "format_text", "normalize_code", "PERSONAS", "THRESHOLDS", "DISCLAIMER",
           "FORBIDDEN_PHRASES"]
