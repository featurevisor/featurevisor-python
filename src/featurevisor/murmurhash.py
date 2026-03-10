from __future__ import annotations


def murmurhash_v3(key: object, seed: int) -> int:
    data = str(key).encode()
    length = len(data)
    remainder = length & 3
    rounded_end = length - remainder
    h1 = seed & 0xFFFFFFFF
    c1 = 0xCC9E2D51
    c2 = 0x1B873593
    index = 0
    while index < rounded_end:
        k1 = data[index] | (data[index + 1] << 8) | (data[index + 2] << 16) | (data[index + 3] << 24)
        index += 4
        k1 = (((k1 & 0xFFFF) * c1) + ((((k1 >> 16) * c1) & 0xFFFF) << 16)) & 0xFFFFFFFF
        k1 = ((k1 << 15) | (k1 >> 17)) & 0xFFFFFFFF
        k1 = (((k1 & 0xFFFF) * c2) + ((((k1 >> 16) * c2) & 0xFFFF) << 16)) & 0xFFFFFFFF
        h1 ^= k1
        h1 = ((h1 << 13) | (h1 >> 19)) & 0xFFFFFFFF
        h1b = (((h1 & 0xFFFF) * 5) + ((((h1 >> 16) * 5) & 0xFFFF) << 16)) & 0xFFFFFFFF
        h1 = ((h1b & 0xFFFF) + 0x6B64 + ((((h1b >> 16) + 0xE654) & 0xFFFF) << 16)) & 0xFFFFFFFF

    k1 = 0
    if remainder == 3:
        k1 ^= data[index + 2] << 16
    if remainder >= 2:
        k1 ^= data[index + 1] << 8
    if remainder >= 1:
        k1 ^= data[index]
        k1 = (((k1 & 0xFFFF) * c1) + ((((k1 >> 16) * c1) & 0xFFFF) << 16)) & 0xFFFFFFFF
        k1 = ((k1 << 15) | (k1 >> 17)) & 0xFFFFFFFF
        k1 = (((k1 & 0xFFFF) * c2) + ((((k1 >> 16) * c2) & 0xFFFF) << 16)) & 0xFFFFFFFF
        h1 ^= k1

    h1 ^= length
    h1 ^= h1 >> 16
    h1 = (((h1 & 0xFFFF) * 0x85EBCA6B) + ((((h1 >> 16) * 0x85EBCA6B) & 0xFFFF) << 16)) & 0xFFFFFFFF
    h1 ^= h1 >> 13
    h1 = (((h1 & 0xFFFF) * 0xC2B2AE35) + ((((h1 >> 16) * 0xC2B2AE35) & 0xFFFF) << 16)) & 0xFFFFFFFF
    h1 ^= h1 >> 16
    return h1


MurmurHashV3 = murmurhash_v3
