"""An approximation of Node's ``String.prototype.localeCompare`` order.

Pi's ``ls`` sorts ``a.toLowerCase().localeCompare(b.toLowerCase())``: ICU's
root collation (the Unicode Collation Algorithm with CLDR data). The standard
library has no collator, so :func:`locale_compare_key` builds a three-level
key from a table measured on Node 24 (ICU 78.3) with ``Intl.Collator``
(sensitivity ``base``, ``accent`` and full) over printable ASCII, Latin-1,
Latin Extended-A/B, General Punctuation, currency signs, Greek and Cyrillic:

1. Primary: the character's rank in the table (whitespace, punctuation and
   symbols, currency, digits, Latin, Greek, Cyrillic). ICU has no numeric
   collation, so ``10 < 9``. Expanding letters (``ß`` = ``ss``, ``æ`` =
   ``ae``, ...) weigh as their two letters.
2. Secondary: the accent rank among the characters sharing a primary
   (``a < á < à < ... < ä``), then any combining marks left after NFC; an
   expansion carries ICU's extra secondary element, so ``ss < ß`` and
   ``áe < æ``.
3. Tertiary: variants that differ only below the accent level.

Characters outside the table: other decimal digits weigh as the ASCII digit
of the same value, accented letters as their base letter, compatibility forms
(fullwidth, circled, ...) as what they decompose to at the tertiary level,
other letters after the table in ICU's script order (Hangul before kana, Han
last) and then by code point, and other symbols just before the currency
signs. Format characters and lone marks are ignorable.

This is an approximation: without ICU, characters outside the table can sort
differently from Node within one script. The measured table covers the
characters file names mostly use.
Full ties keep the input order (Python's sort is stable, like JavaScript's).
``tests/test_native_tools_collation.py`` pins Node's order for a fixture.
"""

from __future__ import annotations

import unicodedata
from functools import cache

# `rank.secondary.tertiary:codepoint,...` (hex), generated from Node's ICU.
_TABLE = """
0.0.0:2028 1.0.0:2029 2.0.0:20 2.0.1:a0,202f 3.0.0:203e 4.0.0:5f 5.0.0:2017
6.0.0:2d 7.0.0:2010 7.0.1:2011 8.0.0:2012 9.0.0:2013 10.0.0:2014 11.0.0:2015
12.0.0:2053 13.0.0:2c 14.0.0:3b 15.0.0:204f 16.0.0:3a 17.0.0:21 18.0.0:a1
19.0.0:3f 20.0.0:bf 21.0.0:203d 22.0.0:2e 22.0.1:2024 23.0.0:b7 24.0.0:2055
25.0.0:2056 26.0.0:2058 27.0.0:2059 28.0.0:205a 29.0.0:205b 30.0.0:205c
31.0.0:205d 32.0.0:205e 33.0.0:27 33.1.0:2018 33.2.0:2019 33.3.0:201a
33.4.0:201b 34.0.0:2039 35.0.0:203a 36.0.0:22 36.1.0:201c 36.2.0:201d
36.3.0:201e 36.4.0:201f 37.0.0:ab 38.0.0:bb 39.0.0:28 40.0.0:29 41.0.0:5b
42.0.0:5d 43.0.0:7b 44.0.0:7d 45.0.0:2045 46.0.0:2046 47.0.0:2016 48.0.0:a7
49.0.0:b6 50.0.0:204b 51.0.0:40 52.0.0:2a 53.0.0:204e 54.0.0:2051 55.0.0:2f
56.0.0:5c 57.0.0:26 58.0.0:204a 59.0.0:23 60.0.0:25 61.0.0:2030 62.0.0:2031
63.0.0:2020 64.0.0:2021 65.0.0:2022 66.0.0:2023 67.0.0:2027 68.0.0:2043
69.0.0:204c 70.0.0:204d 71.0.0:2032 72.0.0:2035 73.0.0:2038 74.0.0:203b
75.0.0:203f 76.0.0:2054 77.0.0:2040 78.0.0:2050 79.0.0:2041 80.0.0:2042
81.0.0:60 82.0.0:b4 83.0.0:5e 84.0.0:af 85.0.0:a8 86.0.0:b8 87.0.0:b0
88.0.0:a9 89.0.0:ae 90.0.0:2b 91.0.0:b1 92.0.0:f7 93.0.0:d7 94.0.0:3c
95.0.0:3d 96.0.0:3e 97.0.0:ac 98.0.0:7c 99.0.0:a6 100.0.0:7e 101.0.0:2052
102.0.0:2044 103.0.0:a4 104.0.0:a2 105.0.0:24 106.0.0:a3 107.0.0:a5
108.0.0:20a0 109.0.0:20a1 110.0.0:20a2 111.0.0:20a3 112.0.0:20a4
113.0.0:20a5 114.0.0:20a6 115.0.0:20a9 116.0.0:20aa 117.0.0:20ab
118.0.0:20ac 119.0.0:20ad 120.0.0:20ae 121.0.0:20af 122.0.0:20b0
123.0.0:20b1 124.0.0:20b2 125.0.0:20b3 126.0.0:20b4 127.0.0:20b5
128.0.0:20b6 129.0.0:20b7 130.0.0:20b8 131.0.0:20b9 132.0.0:20ba
133.0.0:20bb 134.0.0:20bc 135.0.0:20bd 136.0.0:20be 137.0.0:20bf 138.0.0:30
139.0.0:31 139.0.1:b9 140.0.0:32 140.0.1:b2 141.0.0:33 141.0.1:b3 142.0.0:34
143.0.0:35 144.0.0:36 145.0.0:37 146.0.0:38 147.0.0:39 148.0.0:61 148.0.1:aa
148.1.0:e1 148.2.0:e0 148.3.0:103 148.4.0:e2 148.5.0:1ce 148.6.0:e5
148.7.0:1fb 148.8.0:e4 148.9.0:1df 148.10.0:e3 148.11.0:227 148.12.0:1e1
148.13.0:105 148.14.0:101 148.15.0:201 148.16.0:203 149.0.0:2c65 150.0.0:62
151.0.0:180 152.0.0:253 153.0.0:183 154.0.0:63 154.1.0:107 154.2.0:109
154.3.0:10d 154.4.0:10b 154.5.0:e7 155.0.0:23c 156.0.0:188 157.0.0:64
157.1.0:10f 157.2.0:111 157.3.0:f0 158.0.0:256 159.0.0:257 160.0.0:18c
161.0.0:221 162.0.0:65 162.1.0:e9 162.2.0:e8 162.3.0:115 162.4.0:ea
162.5.0:11b 162.6.0:eb 162.7.0:117 162.8.0:229 162.9.0:119 162.10.0:113
162.11.0:205 162.12.0:207 163.0.0:247 164.0.0:1dd 165.0.0:259 166.0.0:25b
167.0.0:66 168.0.0:192 169.0.0:67 169.1.0:1f5 169.2.0:11f 169.3.0:11d
169.4.0:1e7 169.5.0:121 169.6.0:123 170.0.0:1e5 171.0.0:260 172.0.0:263
173.0.0:1a3 174.0.0:68 174.1.0:125 174.2.0:21f 174.3.0:127 175.0.0:195
176.0.0:69 176.1.0:ed 176.2.0:ec 176.3.0:12d 176.4.0:ee 176.5.0:1d0
176.6.0:ef 176.7.0:129 176.8.0:12f 176.9.0:12b 176.10.0:209 176.11.0:20b
177.0.0:131 178.0.0:268 179.0.0:269 180.0.0:6a 180.1.0:135 180.2.0:1f0
181.0.0:237 182.0.0:249 183.0.0:6b 183.1.0:1e9 183.2.0:137 184.0.0:199
185.0.0:6c 185.1.0:13a 185.2.0:13e 185.3.0:13c 185.4.0:142 186.0.0:19a
187.0.0:234 188.0.0:19b 189.0.0:6d 190.0.0:6e 190.1.0:144 190.2.0:1f9
190.3.0:148 190.4.0:f1 190.5.0:146 191.0.0:272 192.0.0:19e 193.0.0:235
194.0.0:14b 195.0.0:6f 195.0.1:ba 195.1.0:f3 195.2.0:f2 195.3.0:14f
195.4.0:f4 195.5.0:1d2 195.6.0:f6 195.7.0:22b 195.8.0:151 195.9.0:f5
195.10.0:22d 195.11.0:22f 195.12.0:231 195.13.0:f8 195.14.0:1ff 195.15.0:1eb
195.16.0:1ed 195.17.0:14d 195.18.0:20d 195.19.0:20f 195.20.0:1a1 196.0.0:254
197.0.0:275 198.0.0:223 199.0.0:70 200.0.0:20a7 201.0.0:1a5 202.0.0:71
203.0.0:24b 204.0.0:138 205.0.0:72 205.1.0:155 205.2.0:159 205.3.0:157
205.4.0:211 205.5.0:213 206.0.0:280 207.0.0:24d 208.0.0:73 208.1.0:15b
208.2.0:15d 208.3.0:161 208.4.0:15f 208.5.0:219 208.6.0:17f 209.0.0:23f
210.0.0:283 211.0.0:1aa 212.0.0:74 212.1.0:165 212.2.0:163 212.3.0:21b
213.0.0:167 214.0.0:2c66 215.0.0:1ab 216.0.0:1ad 217.0.0:288 218.0.0:236
219.0.0:75 219.1.0:fa 219.2.0:f9 219.3.0:16d 219.4.0:fb 219.5.0:1d4
219.6.0:16f 219.7.0:fc 219.8.0:1d8 219.9.0:1dc 219.10.0:1da 219.11.0:1d6
219.12.0:171 219.13.0:169 219.14.0:173 219.15.0:16b 219.16.0:215
219.17.0:217 219.18.0:1b0 220.0.0:289 221.0.0:26f 222.0.0:28a 223.0.0:76
224.0.0:28b 225.0.0:28c 226.0.0:77 226.1.0:175 227.0.0:78 228.0.0:79
228.1.0:fd 228.2.0:177 228.3.0:ff 228.4.0:233 229.0.0:24f 230.0.0:1b4
231.0.0:21d 232.0.0:7a 232.1.0:17a 232.2.0:17e 232.3.0:17c 233.0.0:1b6
234.0.0:225 235.0.0:240 236.0.0:292 236.1.0:1ef 237.0.0:1b9 238.0.0:1ba
239.0.0:fe 240.0.0:1bf 241.0.0:1bb 242.0.0:1a8 243.0.0:1bd 244.0.0:185
245.0.0:242 246.0.0:1c0 247.0.0:1c1 248.0.0:1c2 249.0.0:1c3 250.0.0:3b1
250.1.0:3ac 251.0.0:3b2 252.0.0:3b3 253.0.0:3b4 254.0.0:3b5 254.1.0:3ad
255.0.0:3b6 256.0.0:3b7 256.1.0:3ae 257.0.0:3b8 258.0.0:3b9 258.1.0:3af
258.2.0:3ca 259.0.0:3ba 260.0.0:3bb 261.0.0:3bc 261.0.1:b5 262.0.0:3bd
263.0.0:3be 264.0.0:3bf 265.0.0:3c0 266.0.0:3c1 267.0.0:3c3 267.0.1:3c2
268.0.0:3c4 269.0.0:3c5 269.1.0:3cb 269.2.0:3b0 270.0.0:3c6 271.0.0:3c7
272.0.0:3c8 273.0.0:3c9 274.0.0:430 275.0.0:431 276.0.0:432 277.0.0:433
277.1.0:453 278.0.0:434 279.0.0:452 280.0.0:435 280.1.0:450 280.2.0:451
281.0.0:454 282.0.0:436 283.0.0:437 284.0.0:455 285.0.0:438 285.1.0:45d
286.0.0:456 286.1.0:457 287.0.0:439 288.0.0:458 289.0.0:43a 289.1.0:45c
290.0.0:43b 291.0.0:459 292.0.0:43c 293.0.0:43d 294.0.0:45a 295.0.0:43e
296.0.0:43f 297.0.0:440 298.0.0:441 299.0.0:442 300.0.0:45b 301.0.0:443
301.1.0:45e 302.0.0:444 303.0.0:445 304.0.0:446 305.0.0:447 306.0.0:45f
307.0.0:448 308.0.0:449 309.0.0:44a 310.0.0:44b 311.0.0:44c 312.0.0:44d
313.0.0:44e 314.0.0:44f
"""

# Characters ICU weighs as a sequence. A compatibility form (its NFKD is the
# sequence: `ĳ`, `…`) differs from the sequence at the tertiary level; a
# letter such as `ß` or `æ` adds an extra secondary element.
_EXPANSIONS = {
    "\u00bc": "1\u20444",
    "\u00bd": "1\u20442",
    "\u00be": "3\u20444",
    "\u00e6": "ae",
    "\u00df": "ss",
    "\u0133": "ij",
    "\u0140": "l\u00b7",
    "\u0149": "\u02bcn",
    "\u0153": "oe",
    "\u018d": "zw",
    "\u01be": "ts",
    "\u01c6": "dz",
    "\u01c9": "lj",
    "\u01cc": "nj",
    "\u01e3": "ae",
    "\u01f3": "dz",
    "\u01fd": "ae",
    "\u0238": "db",
    "\u0239": "qp",
    "\u2025": "..",
    "\u2026": "...",
    "\u2033": "\u2032\u2032",
    "\u2034": "\u2032\u2032\u2032",
    "\u2036": "\u2035\u2035",
    "\u2037": "\u2035\u2035\u2035",
    "\u203c": "!!",
    "\u2047": "??",
    "\u2048": "?!",
    "\u2049": "!?",
    "\u2057": "\u2032\u2032\u2032\u2032",
    "\u20a8": "Rs",
}
_EXPANSION_SECONDARY = 1000
_DECOMPOSED_SECONDARY = 500

# ICU's script order after Latin, Greek and Cyrillic, by the first word of the
# Unicode character name (measured by sorting one letter per script on Node).
# Han (`CJK`) comes last; a script missing here sorts just before it.
_SCRIPT_ORDER = (
    "COPTIC GLAGOLITIC GEORGIAN ARMENIAN HEBREW SAMARITAN ARABIC SYRIAC MANDAIC "
    "THAANA NKO TIFINAGH ETHIOPIC DEVANAGARI VEDIC BENGALI GURMUKHI GUJARATI "
    "ORIYA TAMIL TELUGU KANNADA MALAYALAM SINHALA MEETEI SYLOTI SAURASHTRA "
    "SUNDANESE THAI LAO TIBETAN LEPCHA PHAGS-PA LIMBU TAGALOG HANUNOO BUHID "
    "TAGBANWA BUGINESE BATAK REJANG KAYAH MYANMAR KHMER TAI NEW CHAM BALINESE "
    "JAVANESE MONGOLIAN OL CHEROKEE CANADIAN OGHAM RUNIC VAI BAMUM HANGUL "
    "HIRAGANA KATAKANA MASU BOPOMOFO YI LISU"
).split()
_SCRIPT_RANK = {script: rank for rank, script in enumerate(_SCRIPT_ORDER)}
_UNKNOWN_SCRIPT = len(_SCRIPT_ORDER)
_SCRIPT_RANK["CJK"] = _UNKNOWN_SCRIPT + 1


def _parse_table() -> dict[str, tuple[int, int, int]]:
    table: dict[str, tuple[int, int, int]] = {}
    for token in _TABLE.split():
        weights, codepoints = token.split(":")
        primary, secondary, tertiary = (int(part) for part in weights.split("."))
        for codepoint in codepoints.split(","):
            table[chr(int(codepoint, 16))] = (primary, secondary, tertiary)
    return table


_RANKS = _parse_table()
# Symbols outside the table sort after the other symbols, before currency.
_BEFORE_CURRENCY = _RANKS["\u00a4"][0] - 1
_AFTER_TABLE = max(rank for rank, _, _ in _RANKS.values()) + 1

Weight = tuple[int, int]
Element = tuple[Weight, int, int]


@cache
def _char_elements(char: str) -> tuple[Element, ...]:
    """``((primary, secondary, tertiary), ...)`` for one NFC character."""

    if char in _EXPANSIONS:
        return _expansion_elements(char)
    ranked = _RANKS.get(char)
    if ranked is not None:
        primary, secondary, tertiary = ranked
        return (((primary, 0), secondary, tertiary),)
    category = unicodedata.category(char)
    if category == "Cf" or category.startswith("M"):
        return ()
    if category == "Nd":
        digit = _RANKS[str(unicodedata.digit(char))][0]
        return (((digit, 0), 1, 0),)
    decomposed = unicodedata.normalize("NFD", char)
    if decomposed[0] in _RANKS and decomposed[0] != char:
        # An accented letter outside the table: its base, accents after the
        # table's own accent ranks.
        accent = _DECOMPOSED_SECONDARY + sum(ord(mark) for mark in decomposed[1:])
        return (((_RANKS[decomposed[0]][0], 0), accent, 0),)
    compatible = unicodedata.normalize("NFKD", char)
    if compatible != char:
        # A compatibility form (fullwidth, circled, ...) is a tertiary
        # variant of what it decomposes to.
        return tuple(
            (primary, secondary, 1)
            for part in compatible
            for primary, secondary, _ in _char_elements(part)
        )
    if "\u30a1" <= char <= "\u30f6":
        # Katakana is a tertiary variant of the same hiragana.
        ((kana, kana_accent, _),) = _char_elements(chr(ord(char) - 0x60))
        return ((kana, kana_accent, 1),)
    if category.startswith(("L", "N")):
        return (((_AFTER_TABLE + _script_rank(char), ord(char)), 0, 0),)
    return (((_BEFORE_CURRENCY, ord(char) + 1), 0, 0),)


def _script_rank(char: str) -> int:
    script = unicodedata.name(char, "").split(" ")[0]
    return _SCRIPT_RANK.get(script, _UNKNOWN_SCRIPT)


def _expansion_elements(char: str) -> tuple[Element, ...]:
    sequence = _EXPANSIONS[char]
    elements = [
        element for part in sequence.lower() for element in _char_elements(part)
    ]
    if unicodedata.normalize("NFKD", char) == sequence:
        return tuple((primary, secondary, 1) for primary, secondary, _ in elements)
    marks = unicodedata.normalize("NFD", char)[1:]
    accent = _EXPANSION_SECONDARY + sum(ord(mark) for mark in marks)
    (primary, _, tertiary), *rest = elements
    return ((primary, accent, tertiary), *rest)


def locale_compare_key(
    text: str,
) -> tuple[tuple[Weight, ...], tuple[int, ...], tuple[int, ...]]:
    """A sort key approximating ``localeCompare`` (ICU root collation)."""

    primaries: list[Weight] = []
    secondaries: list[int] = []
    tertiaries: list[int] = []
    for char in unicodedata.normalize("NFC", text):
        elements = _char_elements(char)
        if not elements:
            if unicodedata.category(char).startswith("M"):
                # A mark NFC could not compose still accents its character;
                # a leading one is a secondary weight of its own.
                if secondaries:
                    secondaries[-1] += ord(char)
                else:
                    secondaries.append(ord(char))
            continue
        for primary, secondary, tertiary in elements:
            primaries.append(primary)
            secondaries.append(secondary)
            tertiaries.append(tertiary)
    return tuple(primaries), tuple(secondaries), tuple(tertiaries)


def ls_sort_key(
    name: str,
) -> tuple[tuple[Weight, ...], tuple[int, ...], tuple[int, ...]]:
    """Pi ``ls``: ``a.toLowerCase().localeCompare(b.toLowerCase())``."""

    return locale_compare_key(name.lower())


__all__ = ["locale_compare_key", "ls_sort_key"]
