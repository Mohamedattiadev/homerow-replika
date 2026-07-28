"""Hint label generation.

Labels must be prefix-free, so that finishing a label is never ambiguous with
starting a longer one, and short labels should land on the elements you are
most likely to want.
"""

from . import config


def generate(count, alphabet=None):
    """`count` prefix-free labels, shortest first."""
    alphabet = alphabet or config.HINT_ALPHABET
    base = len(alphabet)
    if count <= 0:
        return []
    if base < 2:
        raise ValueError("HINT_ALPHABET needs at least two characters")

    # Shortest width that can address every element.
    width = 1
    while base ** width < count:
        width += 1

    # Using every label of `width` would waste short ones, so promote as many
    # leading labels to width-1 as we can still afford. Each promotion costs
    # one prefix, which would have covered `base` labels of the full width.
    short = 0
    if width > 1:
        capacity = base ** width
        while short + 1 <= base ** (width - 1) and \
                (capacity - (short + 1) * base) >= (count - (short + 1)):
            short += 1

    labels = []
    for i in range(short):
        labels.append(_encode(i, alphabet, width - 1))
    for i in range(count - short):
        labels.append(_encode(short * base + i, alphabet, width))
    return labels


def _encode(value, alphabet, width):
    base = len(alphabet)
    chars = []
    for _ in range(width):
        chars.append(alphabet[value % base])
        value //= base
    return "".join(reversed(chars))


def assign(elements):
    """Pair each element with a label, top-left elements getting short ones."""
    order = sorted(range(len(elements)),
                   key=lambda i: (elements[i].y, elements[i].x))
    labels = generate(len(elements))
    out = [None] * len(elements)
    for label, idx in zip(labels, order):
        out[idx] = label
    return out
