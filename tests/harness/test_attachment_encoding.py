import pytest

from gigaloom.attachments import (
    AttachmentCharsetEvidence,
    TextAttachmentDecodeError,
    attachment_from_dict,
    attachment_to_dict,
    charset_evidence_for,
    decode_attachment_text,
    decode_attachment_text_prefix,
    truncate_utf8_text,
)


@pytest.mark.parametrize(
    ("charset", "bom", "codec"),
    (
        ("utf-8-sig", b"\xef\xbb\xbf", "utf-8"),
        ("utf-16-le", b"\xff\xfe", "utf-16-le"),
        ("utf-16-be", b"\xfe\xff", "utf-16-be"),
        ("utf-32-le", b"\xff\xfe\x00\x00", "utf-32-le"),
        ("utf-32-be", b"\x00\x00\xfe\xff", "utf-32-be"),
    ),
)
def test_decodes_bom_variants_exactly(charset, bom, codec):
    text = "Привет, GigaLoom!\n"

    result = decode_attachment_text(bom + text.encode(codec))

    assert result.text == text
    assert result.charset == charset
    assert result.confidence_class == "exact"
    assert result.bom_present is True
    assert "�" not in result.text


@pytest.mark.parametrize("codec", ("utf-16-le", "utf-16-be", "utf-32-le", "utf-32-be"))
def test_decodes_conservative_bomless_unicode(codec):
    text = "# Комментарий\nprint('hello')\n"

    result = decode_attachment_text(text.encode(codec))

    assert result.text == text
    assert result.charset == codec
    assert result.confidence_class in {"strong", "conservative"}
    assert result.bom_present is False


@pytest.mark.parametrize("codec", ("windows-1251", "koi8-r"))
def test_decodes_legacy_russian_text_with_round_trip(codec):
    text = "Привет мир, это проверка текста и комментария.\n"

    result = decode_attachment_text(text.encode(codec))

    assert result.text == text
    assert result.charset == codec
    assert result.confidence_class in {"strong", "conservative"}
    assert result.text.encode(codec) == text.encode(codec)


def test_utf8_fast_path_is_exact_and_preserves_text():
    text = "hello, мир\nprint('ok')\n"

    result = decode_attachment_text(text.encode())

    assert result.text == text
    assert result.charset == "utf-8"
    assert result.confidence_class == "exact"
    assert result.bom_present is False


@pytest.mark.parametrize(
    "payload",
    (
        b"\x89PNG\r\n\x1a\n" + b"\x00" * 32,
        b"PK\x03\x04" + b"archive bytes",
        b"plain\x00text\x00masquerade",
        b"\xff\xfe\x00",
        bytes(range(256)),
    ),
)
def test_rejects_binary_or_malformed_payload_without_replacement(payload):
    with pytest.raises(TextAttachmentDecodeError):
        decode_attachment_text(payload)


def test_charset_evidence_round_trips_and_old_attachment_remains_readable():
    payload = "Привет".encode("windows-1251")
    decoded = decode_attachment_text(payload)
    evidence = charset_evidence_for(payload, decoded)
    attachment = attachment_from_dict(
        {
            "id": "att_old",
            "session_id": "sess_old",
            "kind": "text",
            "charset_evidence": {
                "charset": evidence.charset,
                "confidence_class": evidence.confidence_class,
                "bom_present": evidence.bom_present,
                "truncated": evidence.truncated,
                "replacement_count": evidence.replacement_count,
                "failure_reason": evidence.failure_reason,
                "source_digest": evidence.source_digest,
            },
        }
    )

    assert attachment.charset_evidence == evidence
    assert attachment_to_dict(attachment)["charset_evidence"]["charset"] == (
        "windows-1251"
    )

    legacy = attachment_from_dict(
        {"id": "att_old", "session_id": "sess_old", "kind": "text"}
    )
    assert legacy.charset_evidence is None
    assert "charset_evidence" not in attachment_to_dict(legacy)


def test_utf8_truncation_ends_on_character_boundary_without_replacement():
    text, truncated = truncate_utf8_text("абв", 5)

    assert text == "аб"
    assert truncated is True
    assert "�" not in text


def test_bounded_utf8_prefix_does_not_fall_back_to_legacy_encoding():
    source = ("a" * 8191 + "я").encode("utf-8")[:8192]

    decoded = decode_attachment_text_prefix(source)

    assert decoded.charset == "utf-8"
    assert decoded.text == "a" * 8191


def test_charset_evidence_type_is_content_free():
    evidence = AttachmentCharsetEvidence(
        charset=None,
        confidence_class=None,
        bom_present=False,
        truncated=False,
        replacement_count=0,
        failure_reason="binary_content",
        source_digest="a" * 64,
    )

    assert "text" not in evidence.__dict__
