import json
import sys

# import bencodepy - available if you need it!
# import requests - available if you need it!


# Examples:
#
# - decode_string(b"5:hello") -> b"hello"
# - decode_string(b"10:hello12345") -> b"hello12345"


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


# Examples:
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


# Examples:
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

    raise NotImplementedError("Only strings are supported at the moment")


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
    else:
        raise NotImplementedError(f"Unknown command {command}")


if __name__ == "__main__":
    main()
