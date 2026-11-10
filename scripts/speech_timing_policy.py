"""Shared, read-only speech loads and punctuation references.

Punctuation suggests a possible pause; it never creates a mandatory silence.
Only actual reading units and an author's explicit internal pause contribute
to the capacity floor. No helper here selects wording, timing or performance.
"""
from decimal import Decimal
import re
import unicodedata


SPEEDS = {'常速': (5.2, 5.8), '快节奏': (5.8, 6.4), '情绪慢速': (4.0, 4.8)}
PAUSE_RANGES = {'comma': (.1, .2), 'sentence': (.1, .25)}
DEFAULT_RATE = sum(SPEEDS['常速']) / 2
CAPACITY_RATE = 6.4
SEMANTIC_PAUSE_ESTIMATE = .26
PUNCTUATION = '，、,。？！?!；;…—～~：:'

# A complete number/English token owns its internal punctuation, even before
# its required pronunciation has been supplied by the author.
READING_TOKEN = re.compile(
    r"[+\-]?(?:[0-9０-９]{1,3}(?:[,，][0-9０-９]{3})+(?:[.．][0-9０-９]+)?"
    r"|[A-Za-z0-9Ａ-Ｚａ-ｚ０-９]+(?:[.．:/\-'’&＆][A-Za-z0-9Ａ-Ｚａ-ｚ０-９]+)*)(?:\+\+|[%％])?"
)
_PUNCTUATION_CLUSTER = re.compile('[' + re.escape(PUNCTUATION) + ']+')


def character_count(text):
    """Raw screening count, not a pronunciation inference."""
    return sum(unicodedata.category(char)[0] in ('L', 'N') for char in text)


def reading_units(text, readings=()):
    """Count validated, source-local readings and return every missing token."""
    declarations = {(row['text'], row['occurrence']): row['units'] for row in readings}
    occurrences, missing, units, cursor = {}, [], 0, 0
    for match in READING_TOKEN.finditer(text):
        token = match.group()
        occurrences[token] = occurrences.get(token, 0) + 1
        key = (token, occurrences[token])
        units += character_count(text[cursor:match.start()])
        if key not in declarations:
            missing.append({'text': token, 'occurrence': key[1]})
        else:
            units += len(declarations[key])
        cursor = match.end()
    return units + character_count(text[cursor:]), missing


def capacity_seconds(units, internal_pause=0):
    """Known policy floor; the caller validates the author's pause value."""
    return Decimal(units) / Decimal(str(CAPACITY_RATE)) + Decimal(str(internal_pause))


def punctuation_reference(text):
    """Scan one unchanged utterance, excluding punctuation inside reading tokens.

    Offsets use Python string indices into the exact supplied text. Clusters
    are candidates, not assertions that a pause occurs. Semantic marks retain
    their context flag even when adjacent sentence punctuation supplies a
    reference range. The reference must never be used as a capacity minimum.
    """
    clusters = []
    cursor = 0
    gaps = []
    for token in READING_TOKEN.finditer(text):
        gaps.append((cursor, token.start()))
        cursor = token.end()
    gaps.append((cursor, len(text)))
    for left, right in gaps:
        for match in _PUNCTUATION_CLUSTER.finditer(text, left, right):
            marks = match.group()
            semantic = any(char in '；;…—～~：:' for char in marks)
            if any(char in '。？！?!' for char in marks):
                kind, bounds = 'sentence', PAUSE_RANGES['sentence']
            elif semantic:
                kind, bounds = 'semantic', None
            else:
                kind, bounds = 'comma', PAUSE_RANGES['comma']
            reference = (float(sum((Decimal(str(value)) for value in bounds), Decimal(0)) / 2)
                         if bounds else SEMANTIC_PAUSE_ESTIMATE)
            clusters.append({'start': match.start(), 'end': match.end(), 'text': marks,
                             'kind': kind, 'reference_range': list(bounds) if bounds else None,
                             'reference_seconds': reference, 'requires_context': semantic})
    total = sum((Decimal(str(row['reference_seconds'])) for row in clusters), Decimal(0))
    return {'clusters': clusters, 'reference_seconds': float(total),
            'blocking': False, 'basis': 'punctuation_candidates_only'}


def pause_seconds(text):
    """Optional natural-estimate weight, never an inferred mandatory pause."""
    return round(punctuation_reference(text)['reference_seconds'], 3)


def punctuation_weights(fragments):
    """Legacy estimate weights, assigning each whole-utterance cluster once.

    This only locates a reference weight; it does not allocate a real pause.
    A cluster crossing a fragment boundary belongs to its starting fragment.
    """
    reference = punctuation_reference(''.join(fragments))
    weights, cursor = [], 0
    for text in fragments:
        end = cursor + len(text)
        weights.append(sum(row['reference_seconds'] for row in reference['clusters']
                           if cursor <= row['start'] < end))
        cursor = end
    return weights
