"""PRE_EXECUTION safety plugins: pii_redactor (10), injection_shield (20)."""
from __future__ import annotations
import re
import unicodedata
from app.plugins_core import Blocked, Plugin, Stage

_ZW = dict.fromkeys(map(ord, "​‌‍⁠﻿­"), None)
# common Cyrillic/Greek confusables -> Latin (NFKC does not fold these)
_CONF = str.maketrans({"а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "х": "x", "у": "y", "і": "i", "ѕ": "s", "ј": "j",
                       "ԁ": "d", "ɡ": "g", "ο": "o", "α": "a", "ε": "e", "ν": "v", "ι": "i", "κ": "k", "τ": "t",
                       "А": "A", "Е": "E", "О": "O", "Р": "P", "С": "C", "Х": "X", "І": "I", "Ѕ": "S"})
_WORDNUM = {"zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
            "seven": "7", "eight": "8", "nine": "9"}


def normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).translate(_ZW).translate(_CONF)


_AT = r"(?:@|\s*\[\s*at\s*\]\s*|\s*\(\s*at\s*\)\s*|\s+at\s+|\s*\{\s*at\s*\}\s*)"
_DOT = r"(?:\.|\s*\[\s*dot\s*\]\s*|\s*\(\s*dot\s*\)\s*|\s+dot\s+|\s*\{\s*dot\s*\}\s*)"
_EMAIL = re.compile(rf"[\w.+-]+{_AT}[\w-]+(?:{_DOT}[\w-]+)*{_DOT}[a-z]{{2,}}\b", re.I)
_SSN = re.compile(r"(?<!\d)\d{3}[\s.\-_]?\d{2}[\s.\-_]?\d{4}(?!\d)")
_SPACED = re.compile(r"(?<![\w-])(?:\d[\s.\-_]{1,3}){8,17}\d(?![\w-])")
_CARD = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")
_PHONE = re.compile(r"(?<![\w-])(?:\+?\d{1,3}[\s.\-]?)?\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}(?![\w-])")
_WORDS = re.compile(r"\b(?:(?:zero|oh|one|two|three|four|five|six|seven|eight|nine)[\s,.-]+){8,}(?:zero|oh|one|two|three|four|five|six|seven|eight|nine)\b", re.I)


def redact(text: str) -> str:
    t = normalize(text)
    t = _WORDS.sub("[NUMBER]", t)
    t = _EMAIL.sub("[EMAIL]", t)
    t = _SSN.sub("[SSN]", t)
    t = _CARD.sub("[CARD]", t)
    t = _SPACED.sub("[NUMBER]", t)
    t = _PHONE.sub("[PHONE]", t)
    return t


def has_pii(text: str) -> bool:
    return redact(text) != normalize(text)


def pii_redactor(ctx):
    ctx["message"] = redact(ctx["message"])
    return ctx


_INJECTION = [
    r"\b(ignore|disregard|forget|override|bypass|skip|drop)\b.{0,40}\b(previous|prior|above|earlier|all|any|your|the|these|those|safety|system)\b.{0,40}\b(instructions?|rules?|guidelines?|polic(?:y|ies)|prompts?|restrictions?|guardrails?|filters?|constraints?)\b",
    r"\b(reveal|print|show|display|repeat|output|leak|dump|disclose|expose|recite|echo|tell me|give me|share)\b.{0,50}\b(system|hidden|initial|original|secret|internal|developer)\b.{0,15}\b(prompt|instructions?|message|configuration|config|rules)\b",
    r"\bwhat (?:are|were|is) your (?:system |initial |original |hidden )?(instructions|prompt|rules|guidelines)\b",
    r"\b(you are|you're|act as|pretend (?:to be|you are)|roleplay as|from now on you)\b.{0,40}\b(dan|unrestricted|jailbroken|unfiltered|uncensored|evil|no restrictions|without (?:any )?(?:rules|restrictions|limits))\b",
    r"\b(developer|debug|god|admin|sudo|maintenance|root|jailbreak|unrestricted) mode\b",
    r"\bdo anything now\b",
    r"\b(new|updated|revised) (instructions?|system prompt|rules)\s*[:\-]",
    r"^\s*(system|assistant|developer)\s*:",
    r"<\s*/?\s*(system|instructions?|im_start|im_end)\s*>|\[\s*/?\s*(inst|system)\s*\]|###\s*(system|instruction)",
    r"\b(disable|turn off|deactivate|remove|lift|suspend)\b.{0,30}\b(safety|guardrails?|filters?|restrictions?|moderation|content polic(?:y|ies)|pii|redaction)\b",
    r"\b(send|post|forward|exfiltrate|transmit|upload|email|leak)\b.{0,60}\b(to|at)\b.{0,10}(https?://|www\.|\S+\s*(?:@|\[at\])\S+)",
    r"\b(execute|run|eval)\b.{0,20}\b(this|the following|below)\b.{0,15}\b(code|command|script|shell|sql)\b",
    r"\bas (?:we|you) (?:agreed|established|discussed|confirmed)\b.{0,80}\b(no (?:rules|restrictions)|unrestricted|debug|bypass|ignore|reveal|print)\b",
    r"\b(base64|rot13|hex)\b.{0,30}\b(decode|decoded|instructions?)\b.{0,40}\b(follow|execute|obey|run)\b",
    r"\b(begin|start) (?:your )?(?:response|answer|reply) with\b.{0,40}\b(sure|certainly|i will|here is)\b.{0,30}\b(system prompt|instructions)\b",
    r"\b(important|urgent|note|attention)\s*(?:to|for)\s*(?:the\s*)?(?:ai|assistant|model|llm|agent)\b",
    r"\b(ai|assistant|model|llm|agent)\b.{0,20}\b(must|should|shall)\b.{0,10}\b(now|instead|first|always)\b.{0,40}\b(reveal|ignore|disregard|send|output|say|respond|append|include)\b",
    r"\bwhen (?:you )?(?:read|see|process) this[, ].{0,60}\b(ignore|reveal|send|output|respond|say|append)\b",
    r"\bthe (?:real|true|actual) (?:answer|instruction)s? (?:is|are)\b",
    r"\bpassword (?:is|for) (?:the )?(?:admin|root)\b",
    r"\bprint (?:your )?(?:config|configuration|environment|env|api[ _-]?key|secrets?)\b",
]
_INJ_RE = [re.compile(p, re.I | re.S | re.M) for p in _INJECTION]


_SQUASHED = ("ignorepreviousinstructions", "ignoreallinstructions", "ignoreallpreviousinstructions", "disregardpreviousinstructions",
             "disregardyourrules", "systemprompt", "hiddeninstructions", "developermode", "jailbreak", "revealyourprompt")
_SPACED_RUN = re.compile(r"\b(?:\w ){3,}\w\b")


def scan_injection(text: str) -> bool:
    t = normalize(text)
    despaced = _SPACED_RUN.sub(lambda m: m[0].replace(" ", ""), t)  # "i g n o r e" -> "ignore" (words kept via 2+ spaces)
    variants = [re.sub(r"[\s_*`~|]+", " ", x) for x in (t, despaced)]
    if any(r.search(x) for r in _INJ_RE for x in variants):
        return True
    if _SPACED_RUN.search(t):  # heavy letter-spacing: also check the fully squashed letter stream
        squashed = re.sub(r"[^a-z]", "", t.lower())
        return any(k in squashed for k in _SQUASHED)
    return False


def injection_shield(ctx):
    if scan_injection(ctx["message"]):
        raise Blocked("injection_shield", "prompt injection detected")
    return ctx


def default_plugins() -> list[Plugin]:
    return [
        Plugin("pii_redactor", Stage.PRE, 10, pii_redactor),
        Plugin("injection_shield", Stage.PRE, 20, injection_shield),
    ]
