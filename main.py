import json
import sys
import hashlib

# import bencodepy - available if you need it!
# import requests - available if you need it!


# Examples:
#
# - decode_string(b"5:hello") -> b"hello"
# - decode_string(b"10:hello12345") -> b"hello12345"

SHA1_SIZE = 20

def decode_string(bencoded_value: bytes, start: int) -> tuple[bytes, int]:
    colon_index = bencoded_value.find(b":", start)
    if colon_index == -1:
        raise ValueError("Invalid encoded value: expected ':'")

    length = bencoded_value[start:colon_index]
    if not length.isdigit():
        raise ValueError(f"Invalid encoded value: expected integer got '{length}'")
    length = int(length)

    string_start = colon_index + 1
    string_end = string_start + length

    return bencoded_value[string_start:string_end], string_end


# Examples:
#
# - decode_integer(b"i52e") -> 52
# - decode_integer(b"i-52e") -> -52


def decode_integer(bencoded_value: bytes, start: int) -> tuple[int, int]:
    e_char_index = bencoded_value.find(b"e", start)
    if e_char_index == -1:
        raise ValueError("Invalid encoded value: expected 'e'")

    integer = bencoded_value[start + 1 : e_char_index]

    try:
        integer = int(integer)
    except ValueError:
        raise ValueError("Invalid encoded value: expected valid integer")
    else:
        return integer, e_char_index + 1


# Example:
#
# - decode_list(b"l5:helloi52ee") -> ["hello",52]


def decode_list(bencoded_value: bytes, start: int) -> tuple[list, int]:
    decoded_list = []

    # Start search after 'l'
    curr = start + 1
    while chr(bencoded_value[curr]) != "e":
        value, curr = decode_bencode(bencoded_value, start=curr)
        decoded_list.append(value)

    return decoded_list, curr + 1


# Example:
#
# - decode_dictionary(b"d3:foo3:bar5:helloi52ee") -> {"foo":"bar", "hello": 52}


def decode_dictionary(bencoded_value: bytes, start: int) -> tuple[dict, int]:
    decoded_dict = {}

    # Start search after 'd'
    curr = start + 1
    while chr(bencoded_value[curr]) != "e":
        key, curr = decode_bencode(bencoded_value, start=curr)
        if not isinstance(key, bytes):
            raise ValueError("Keys must be strings")

        if curr >= len(bencoded_value) or chr(bencoded_value[curr]) == "e":
            raise ValueError("Expected value for key in dictionary")

        value, curr = decode_bencode(bencoded_value, start=curr)
        decoded_dict[key.decode()] = value

    return decoded_dict, curr + 1


def decode_bencode(
    bencoded_value: bytes, start: int = 0
) -> tuple[bytes | int | list | dict, int]:
    if chr(bencoded_value[start]).isdigit():
        return decode_string(bencoded_value, start)

    if chr(bencoded_value[start]) == "i":
        return decode_integer(bencoded_value, start)

    if chr(bencoded_value[start]) == "l":
        return decode_list(bencoded_value, start)

    if chr(bencoded_value[start]) == "d":
        return decode_dictionary(bencoded_value, start)

    raise NotImplementedError(
        "Only strings, ints, lists, and dictionaries are supported at the moment"
    )


# Example:
#
# - encode_string("hello") -> b"5:hello"


def encode_string(string: str) -> bytes:
    return f"{len(string)}:{string}".encode()


# Example:
#
# - encode_bytes(b"hello") -> b"5:hello"


def encode_bytes(byte_string: bytes) -> bytes:
    buf = bytearray(f"{len(byte_string)}:".encode())
    buf.extend(byte_string)

    return bytes(buf)


# Examples:
#
# - encode_integer(52) -> b"i52e"
# - encode_integer(-52) -> b"i-52e"


def encode_integer(integer: int) -> bytes:
    return f"i{integer}e".encode()


# Example:
#
# - encode_list(["hello",52]) -> b"l5:helloi52ee"


def encode_list(lst: list) -> bytes:
    buf = bytearray(b"l")

    for item in lst:
        buf.extend(encode_bencode(item))

    buf.extend(b"e")

    return bytes(buf)


# Example:
#
# - encode_dictionary({"foo":"bar", "hello": 52}) -> b"d3:foo3:bar5:helloi52ee"


def encode_dictionary(dictionary: dict) -> bytes:
    buf = bytearray(b"d")

    for key, value in sorted(dictionary.items()):
        if not isinstance(key, str):
            raise ValueError("Bencoded dictionary only allows strings as keys")

        buf.extend(encode_string(key))
        buf.extend(encode_bencode(value))

    buf.extend(b"e")

    return bytes(buf)


def encode_bencode(obj: bytes | str | int | list | dict) -> bytes:

    if isinstance(obj, bytes):
        return encode_bytes(obj)
    if isinstance(obj, str):
        return encode_string(obj)
    if isinstance(obj, int):
        return encode_integer(obj)
    if isinstance(obj, list):
        return encode_list(obj)
    if isinstance(obj, dict):
        return encode_dictionary(obj)

    raise NotImplementedError(
        "Only strings, ints, lists, and dictionaries are supported at the moment"
    )


def main():
    command = sys.argv[1]

    # You can use print statements as follows for debugging, they'll be visible when running tests.
    # print("Logs from your program will appear here!", file=sys.stderr)

    if command == "decode":
        bencoded_value = sys.argv[2].encode()

        # json.dumps() can't handle bytes, but bencoded "strings" need to be
        # bytestrings since they might contain non utf-8 characters.
        #
        # Let's convert them to strings for printing to the console.
        def bytes_to_str(data):
            if isinstance(data, bytes):
                return data.decode()

            raise TypeError(f"Type not serializable: {type(data)}")

        decoded, _ = decode_bencode(bencoded_value)
        print(json.dumps(decoded, default=bytes_to_str))

    elif command == "info":
        file_name = sys.argv[2]

        with open(file_name, "rb") as f:
            data = f.read()

            decoded, _ = decode_bencode(data)

            print(decoded)
            if isinstance(decoded, dict):
                print(f"Tracker URL: {decoded['announce'].decode()}")
                print(f"Length: {decoded['info']['length']}")
                print(
                    f"Info Hash: {hashlib.sha1(encode_bencode(decoded['info'])).hexdigest()}"
                )
                print(f"Piece Length: {decoded['info']['piece length']}")
                print(f"Piece Hashes: {decoded['info']['piece length']}")

                pieces: bytes = decoded['info']['pieces']

                if len(pieces) % SHA1_SIZE != 0:
                    raise ValueError(f"Invalid torrent: pieces field length is not divisible by {SHA1_SIZE}")
                
                for i in range(0, len(pieces), SHA1_SIZE):
                    print(pieces[i:i+SHA1_SIZE].hex())
    else:
        raise NotImplementedError(f"Unknown command {command}")


if __name__ == "__main__":
    main()
