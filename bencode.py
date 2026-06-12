# Support the decoding/encoding of data in Bencode format used by the
# BitTorrent protocol. Bencode supports four basic data types:
#
# - Byte Strings
# - Integers
# - Lists
# - Dictionaries


# Bencoded byte strings are length-prefixed in base ten followed by a colon and the string.
#
# Ex:
#   b"5:hello" corresponds to b"hello"
#   b"10:hello12345" corresponds to b"10:hello12345"


# Bencoded integers are represented by an 'i' followed by the number in base 10 followed by an 'e'.
# All encodings with a leading zero, such as i03e, are invalid, other than i0e, which of course
# corresponds to 0. Integers have no size limitation. i-0e is invalid.
#
# Ex:
#   b"i67e" corresponds to 67
#   b"i-67e" corresponds to -67


# Lists are encoded as an 'l' followed by their elements (also bencoded) followed by an 'e'.
#
# Ex:
#   b"l4:spam4:eggse" corresponds to [b"spam", b"eggs"]
#   b"l5:helloi52ee" corresponds to [b"hello", 52]


# Dictionaries are encoded as a 'd' followed by a list of alternating keys and their corresponding values followed by an 'e'.
# Keys must be strings and appear in sorted order (sorted as raw strings, not alphanumerics).
#
# Ex:
#   b"d3:cow3:moo4:spam4:eggse" corresponds to {b"cow": b"moo", b"spam": b"eggs"}
#   b"d4:spaml1:ai69eee" corresponds to {b"spam": [b"a", 69]}.


class DecodeError(Exception):
    pass


class EncodeError(Exception):
    pass


type DecodedValue = int | bytes | list[DecodedValue] | dict[bytes, DecodedValue]


def decode(data: bytes) -> DecodedValue:
    decoded, end = _decode(data)

    if end != len(data):
        raise DecodeError("Could not decode entire data")

    return decoded


def _decode(data: bytes, start: int = 0) -> tuple[DecodedValue, int]:
    char = chr(data[start])

    if char.isdigit():
        return _decode_byte_string(data, start)
    if char == "i":
        return _decode_integer(data, start)
    if char == "l":
        return _decode_list(data, start)
    if char == "d":
        return _decode_dictionary(data, start)

    raise DecodeError(
        f"Invalid Bencoded data: unexpected char '{char}' at index {start}"
    )


def _decode_byte_string(data: bytes, start: int) -> tuple[bytes, int]:
    colon_idx = data.find(b":", start)
    if colon_idx == -1:
        raise DecodeError("Invalid bencoded byte string: expected ':'")

    length = data[start:colon_idx]
    if not length.isdigit():
        raise DecodeError(
            f"Invalid bencoded byte string: expected base 10 integer, got '{length}'"
        )
    length = int(length)

    str_start = colon_idx + 1
    str_end = str_start + length

    return data[str_start:str_end], str_end


def _decode_integer(data: bytes, index: int) -> tuple[int, int]:
    if data[index] != ord("i"):
        raise DecodeError("Invalid bencoded integer: must start with 'i'")

    e_idx = data.find(b"e", index)
    if e_idx == -1:
        raise DecodeError("Invalid bencoded integer: missing terminating 'e'")

    digits = data[index + 1 : e_idx]
    if len(digits) == 0:
        raise DecodeError("Invalid bencoded integer: 'ie' is not valid integer")

    negative = digits[0] == ord("-")
    digits = digits[1:] if negative else digits

    if len(digits) == 0:
        raise DecodeError("Invalid bencoded integer: 'i-e' is not valid integer")

    if digits == b"0" and negative:
        raise DecodeError("Invalid bencoded integer: 'i-0e' is not valid integer")

    if digits[0] == ord("0"):
        DecodeError("Invalid bencoded integer: no leading zeros allowed")

    if not digits.isdigit():
        DecodeError("Invalid bencoded integer: only characters 0-9 allowed")

    return int(digits), e_idx + 1


def _decode_list(data: bytes, start: int) -> tuple[list[DecodedValue], int]:
    if data[start] != ord("l"):
        raise DecodeError("Invalid bencoded list: must start with 'l'")

    decoded_list = []

    curr = start + 1
    while curr < len(data) and data[curr] != ord("e"):
        value, curr = _decode(data, start=curr)
        decoded_list.append(value)

    if data[curr] != ord("e"):
        raise DecodeError("Invalid bencoded list: missing terminating 'e'")

    return decoded_list, curr + 1


def _decode_dictionary(
    data: bytes, start: int
) -> tuple[dict[bytes, DecodedValue], int]:
    if data[start] != ord("d"):
        raise DecodeError("Invalid bencoded dictionary: must start with 'd'")

    decoded_dict = {}

    curr = start + 1
    while curr < len(data) and data[curr] != ord("e"):
        key, curr = _decode(data, start=curr)

        if not isinstance(key, bytes):
            raise DecodeError("Invalid bencoded dictionary: keys must be byte strings")

        if curr >= len(data) or data[curr] == ord("e"):
            raise DecodeError("Invalid bencoded dictionary: key is missing value")

        value, curr = _decode(data, start=curr)

        decoded_dict[key] = value

    if data[curr] != ord("e"):
        raise DecodeError("Invalid bencoded dictionary: missing terminating 'e'")

    return decoded_dict, curr + 1


def encode(obj) -> bytes:
    if isinstance(obj, (bytes, str)):
        return _encode_byte_string(obj)
    if isinstance(obj, int):
        return _encode_integer(obj)
    if isinstance(obj, list):
        return _encode_list(obj)
    if isinstance(obj, dict):
        return _encode_dictionary(obj)

    raise EncodeError(
        "Only byte strings, ints, lists, and dictionaries can be bencoded"
    )


def _encode_byte_string(obj: bytes | str) -> bytes:
    data = obj.encode() if isinstance(obj, str) else obj

    header = f"{len(data)}:".encode()
    return b"".join([header, data])


def _encode_integer(integer: int) -> bytes:
    return f"i{integer}e".encode()


def _encode_list(lst: list) -> bytes:
    buf = bytearray(b"l")

    for item in lst:
        buf.extend(encode(item))

    buf.extend(b"e")

    return bytes(buf)


def _encode_dictionary(dictionary: dict) -> bytes:
    buf = bytearray(b"d")

    for key, value in sorted(dictionary.items()):
        if not isinstance(key, (str, bytes)):
            raise EncodeError("Bencoded dictionary only allows byte strings as keys")

        buf.extend(_encode_byte_string(key))
        buf.extend(encode(value))

    buf.extend(b"e")

    return bytes(buf)
