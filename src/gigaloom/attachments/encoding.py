"""Deterministic, replacement-free decoding for text attachments."""

from __future__ import annotations

from dataclasses import dataclass
import unicodedata

HEURISTIC_SAMPLE_BYTES = 64 * 1024
_ALLOWED_CONTROLS = frozenset("\t\n\r\f")
_BINARY_SIGNATURES = (
    b"\x7fELF",
    b"MZ",
    b"PK\x03\x04",
    b"PK\x05\x06",
    b"PK\x07\x08",
    b"\x1f\x8b",
    b"BZh",
    b"7z\xbc\xaf\x27\x1c",
    b"\x89PNG\r\n\x1a\n",
    b"\xff\xd8\xff",
    b"GIF87a",
    b"GIF89a",
    b"%PDF-",
)
_COMMON_RUSSIAN_FRAGMENTS = (
    "ст",
    "но",
    "то",
    "на",
    "ен",
    "ов",
    "ни",
    "ра",
    "во",
    "ко",
    "ро",
    "по",
    "пр",
    "ив",
    "ет",
    "ие",
    "ть",
    "ый",
    "ая",
)
_COMMON_RUSSIAN_LETTERS = frozenset("оеаинтсрвлкмдпуяыьгзбчйхжшюцщэфъё")


class TextAttachmentDecodeError(ValueError):
    """Raised when attachment bytes cannot be admitted as supported text."""

    def __init__(self, failure_reason: str) -> None:
        super().__init__(failure_reason)
        self.failure_reason = failure_reason


@dataclass(frozen=True)
class DecodedAttachmentText:
    """Replacement-free decoded attachment text and its decode facts."""

    text: str
    charset: str
    confidence_class: str
    bom_present: bool


def decode_attachment_text(data: bytes) -> DecodedAttachmentText:
    """Decode one supported text attachment or reject it deterministically."""
    payload = bytes(data)
    if _has_binary_signature(payload):
        raise TextAttachmentDecodeError("binary_signature")

    bom_result = _decode_bom(payload)
    if bom_result is not None:
        return bom_result

    utf8_was_valid = False
    try:
        utf8_text = payload.decode("utf-8")
    except UnicodeDecodeError:
        pass
    else:
        utf8_was_valid = True
        if _is_plausible_text(utf8_text):
            return DecodedAttachmentText(
                text=utf8_text,
                charset="utf-8",
                confidence_class="exact",
                bom_present=False,
            )

    unicode_result = _decode_bomless_unicode(payload)
    if unicode_result is not None:
        return unicode_result

    if utf8_was_valid:
        raise TextAttachmentDecodeError("binary_content")

    legacy_result = _decode_legacy_cyrillic(payload)
    if legacy_result is not None:
        return legacy_result
    raise TextAttachmentDecodeError("undecodable_or_binary")


def _decode_bom(payload: bytes) -> DecodedAttachmentText | None:
    bom_variants = (
        (b"\xff\xfe\x00\x00", "utf-32-le"),
        (b"\x00\x00\xfe\xff", "utf-32-be"),
        (b"\xef\xbb\xbf", "utf-8-sig"),
        (b"\xff\xfe", "utf-16-le"),
        (b"\xfe\xff", "utf-16-be"),
    )
    for bom, charset in bom_variants:
        if not payload.startswith(bom):
            continue
        try:
            text = payload[len(bom) :].decode(charset.removesuffix("-sig"))
        except UnicodeDecodeError as exc:
            raise TextAttachmentDecodeError("malformed_bom_encoding") from exc
        if not _is_plausible_text(text):
            raise TextAttachmentDecodeError("binary_content")
        return DecodedAttachmentText(
            text=text,
            charset=charset,
            confidence_class="exact",
            bom_present=True,
        )
    return None


def _decode_bomless_unicode(payload: bytes) -> DecodedAttachmentText | None:
    sample = payload[:HEURISTIC_SAMPLE_BYTES]
    candidates: list[tuple[float, str, str]] = []
    for charset, width in (
        ("utf-32-le", 4),
        ("utf-32-be", 4),
        ("utf-16-le", 2),
        ("utf-16-be", 2),
    ):
        if not payload or len(payload) % width:
            continue
        structural_score = _unicode_structure_score(sample, charset, width)
        if structural_score < 0.82:
            continue
        try:
            text = payload.decode(charset)
        except UnicodeDecodeError:
            continue
        if _is_plausible_text(text):
            candidates.append((structural_score, charset, text))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    best_score, charset, text = candidates[0]
    if len(candidates) > 1 and best_score - candidates[1][0] < 0.08:
        return None
    return DecodedAttachmentText(
        text=text,
        charset=charset,
        confidence_class="strong" if best_score >= 0.94 else "conservative",
        bom_present=False,
    )


def _unicode_structure_score(sample: bytes, charset: str, width: int) -> float:
    units = len(sample) // width
    if units < 2:
        return 0.0
    lanes = [sample[offset : units * width : width] for offset in range(width)]
    if charset == "utf-16-le":
        structural = _restricted_lane_ratio(lanes[1], frozenset({0x00, 0x04}))
        active = lanes[0]
    elif charset == "utf-16-be":
        structural = _restricted_lane_ratio(lanes[0], frozenset({0x00, 0x04}))
        active = lanes[1]
    elif charset == "utf-32-le":
        structural = min(_zero_ratio(lanes[2]), _zero_ratio(lanes[3]))
        active = lanes[0] + lanes[1]
    else:
        structural = min(_zero_ratio(lanes[0]), _zero_ratio(lanes[1]))
        active = lanes[2] + lanes[3]
    active_signal = sum(byte != 0 for byte in active) / max(len(active), 1)
    return structural * 0.9 + min(active_signal, 0.5) * 0.2


def _decode_legacy_cyrillic(payload: bytes) -> DecodedAttachmentText | None:
    candidates: list[tuple[float, str, str]] = []
    for charset in ("windows-1251", "koi8-r"):
        try:
            text = payload.decode(charset)
        except UnicodeDecodeError:
            continue
        if text.encode(charset) != payload or not _is_plausible_text(text):
            continue
        score = _russian_plausibility(text)
        if score >= 0.58:
            candidates.append((score, charset, text))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    best_score, charset, text = candidates[0]
    if len(candidates) > 1 and best_score - candidates[1][0] < 0.12:
        return None
    return DecodedAttachmentText(
        text=text,
        charset=charset,
        confidence_class="strong" if best_score >= 0.78 else "conservative",
        bom_present=False,
    )


def _russian_plausibility(text: str) -> float:
    letters = [char for char in text if char.isalpha()]
    if len(letters) < 3:
        return 0.0
    cyrillic = [char for char in letters if "CYRILLIC" in unicodedata.name(char, "")]
    if len(cyrillic) / len(letters) < 0.45:
        return 0.0
    lowered = text.lower()
    common_letters = sum(char in _COMMON_RUSSIAN_LETTERS for char in lowered)
    fragment_hits = sum(
        lowered.count(fragment) for fragment in _COMMON_RUSSIAN_FRAGMENTS
    )
    mixed_case_penalty = sum(
        previous.islower() and current.isupper()
        for previous, current in zip(text, text[1:])
        if previous.isalpha() and current.isalpha()
    )
    base = common_letters / max(len(cyrillic), 1)
    fragment_bonus = min(fragment_hits / max(len(cyrillic), 1), 0.3)
    penalty = min(mixed_case_penalty / max(len(cyrillic), 1), 0.5)
    return base * 0.75 + fragment_bonus - penalty


def _is_plausible_text(text: str) -> bool:
    if "\ufffd" in text or "\x00" in text:
        return False
    if not text:
        return True
    controls = sum(
        unicodedata.category(char) == "Cc" and char not in _ALLOWED_CONTROLS
        for char in text
    )
    return controls <= min(8, max(1, len(text) // 100))


def _has_binary_signature(payload: bytes) -> bool:
    return any(payload.startswith(signature) for signature in _BINARY_SIGNATURES)


def _restricted_lane_ratio(values: bytes, admitted: frozenset[int]) -> float:
    return sum(value in admitted for value in values) / max(len(values), 1)


def _zero_ratio(values: bytes) -> float:
    return values.count(0) / max(len(values), 1)
