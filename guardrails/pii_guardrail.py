import re


class PIIGuardrail:
    """Masks Personal Identifiable Information (PII) from strings.

    Masks the following patterns before they leave the tool layer:

    * **Email addresses** — ``user@example.com`` -> ``***@example.com``
    * **US/Canada phone numbers** — ``555-123-4567`` -> ``***-***-****``
      (also matches ``(555) 123-4567`` and dot/space separators)
    * **International phone numbers** (E.164) — ``+14155552671`` ->
      ``+***********`` (keeps the country code length, masks the rest)
    * **Credit-card numbers** — 13–19 digit Luhn-valid groups
      (separators optional) -> ``****-****-****-****``
    * **US Social Security Numbers** — ``123-45-6789`` -> ``***-**-****``
    * **IPv4 addresses** — ``192.168.1.1`` -> ``***.***.***.***

    Patterns that fail their format check (e.g. credit card numbers that
    do not pass the Luhn checksum) are left untouched to keep the false
    positive rate low.

    The optional NER second pass (``mask_pii_ner``, Phase 2) catches names
    and places regexes cannot see. It fails safe: a missing model leaves
    the text to the regex layer instead of raising.
    """

    EMAIL_REGEX = re.compile(r"([a-zA-Z0-9_.+-]+)@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
    PHONE_REGEX = re.compile(r"\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}")
    # International (E.164-style): + then 7–15 digits total, with
    # optional single spaces or hyphens between groups of 1–4 digits.
    # Made specific enough to require the leading + (not a plain
    # long-distance digit run) and bounded so it can't swallow arbitrary
    # digit runs.
    INTL_PHONE_REGEX = re.compile(
        r"\+\d(?:[ -]?\d){6,14}"
    )
    SSN_REGEX = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
    IPV4_REGEX = re.compile(
        r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}"
        r"(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b"
    )
    CC_GROUP_REGEX = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")

    @classmethod
    def _luhn_valid(cls, digits):
        total = 0
        for i, ch in enumerate(reversed(digits)):
            d = ord(ch) - 48
            if d < 0 or d > 9:
                return False
            if i % 2 == 1:
                d *= 2
                if d > 9:
                    d -= 9
            total += d
        return total % 10 == 0

    @classmethod
    def _mask_credit_card(cls, match):
        raw = match.group(0)
        digits = re.sub(r"\D", "", raw)
        if not (13 <= len(digits) <= 19):
            return raw
        if not cls._luhn_valid(digits):
            return raw
        return "****-****-****-****"

    @classmethod
    def _mask_intl_phone(cls, match):
        raw = match.group(0)
        digits = re.sub(r"\D", "", raw)
        if len(digits) < 8 or len(digits) > 15:
            return raw
        return "+" + "*" * (len(digits) - 1)

    @classmethod
    def mask_pii(cls, text):
        if not isinstance(text, str):
            text = str(text)

        def email_replacer(match):
            full_email = match.group(0)
            parts = full_email.split("@")
            return f"***@{parts[1]}"

        text = cls.EMAIL_REGEX.sub(email_replacer, text)
        # International phone first so the trailing 10 digits of an E.164
        # number are not picked up by the US/Canada regex below.
        text = cls.INTL_PHONE_REGEX.sub(cls._mask_intl_phone, text)
        text = cls.PHONE_REGEX.sub("***-***-****", text)
        text = cls.SSN_REGEX.sub("***-**-****", text)
        text = cls.IPV4_REGEX.sub("***.***.***.***", text)
        text = cls.CC_GROUP_REGEX.sub(cls._mask_credit_card, text)
        return text

    # ------------------------------------------------------------------
    # NER second pass (Phase 2)
    # ------------------------------------------------------------------

    #: Entity types masked by ``mask_pii_ner``. PERSON first (highest
    #: precision on name-like data); GPE/ORG included because street-level
    #: addresses and employers leak through regexes untouched. Calibrated
    #: by the spike in Phase 2 — see STATUS.md for measured numbers.
    NER_ENTITIES = ("PERSON", "GPE", "ORG")

    _NER_MODEL = None

    @classmethod
    def _ner_model(cls):
        if cls._NER_MODEL is None:
            import spacy

            cls._NER_MODEL = spacy.load("en_core_web_sm")
        return cls._NER_MODEL

    @classmethod
    def mask_pii_ner(cls, text, entities=None):
        """Mask spaCy named entities (default: PERSON/GPE/ORG) with ``***``.

        Runs right-to-left over spans so offsets stay valid. A missing or
        unloadable model returns the text unchanged (the regex layer still
        stands) instead of raising — masking must never break answering.
        """
        if not isinstance(text, str):
            text = str(text)
        wanted = set(entities or cls.NER_ENTITIES)
        try:
            doc = cls._ner_model()(text)
        except Exception:
            return text
        out = text
        for ent in sorted(doc.ents, key=lambda e: e.start_char, reverse=True):
            if ent.label_ in wanted:
                out = out[:ent.start_char] + "***" + out[ent.end_char:]
        return out
