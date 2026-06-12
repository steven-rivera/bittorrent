from dataclasses import dataclass
from typing import TypeVar, Iterator
import bencode
import hashlib
import requests
import os
import socket


T = TypeVar("T")
SHA1_SIZE = 20

ANNOUNCE_KEY = b"announce"
INFO_KEY = b"info"
PIECE_LEN_KEY = b"piece length"
PIECES_KEY = b"pieces"
NAME_KEY = b"name"
LENGTH_KEY = b"length"

FAILURE_KEY = b"failure reason"
INTERVAL_KEY = b"interval"
PEERS_KEY = b"peers"


class InvalidBencodedData(Exception):
    pass

class TrackerError(Exception):
    pass

class DownloadError(Exception):
    pass


@dataclass
class Info:
    # UTF-8 encoded string which is the suggested name
    # to save the file (or directory) as
    name: bytes

    # Number of bytes in each piece the file is split into
    piece_length: int

    # A string whose length is a multiple of 20. It is subdivided
    # into strings of length 20, each of which is the SHA1 hash
    # of the piece at the corresponding index
    pieces: bytes

    # The length of the file, in bytes
    length: int

    def iter_piece_hashes(self) -> Iterator[bytes]:
        for i in range(0, len(self.pieces), SHA1_SIZE):
            yield self.pieces[i : i + SHA1_SIZE]

    def get_piece_hash(self, index: int) -> bytes:
        start = index * SHA1_SIZE
        end = start + SHA1_SIZE
        return self.pieces[start:end]


@dataclass
class Torrent:
    # The URL of the tracker
    announce: bytes

    # Info dictionary
    info: Info

    # SHA-1 hash of bencoded info dictionary
    info_hash: bytes


@dataclass
class Peer:
    # IPv4 addr of peer
    ip_addr: str

    # Port number to conn
    port: int


def require_type(
    dictionary: dict[bytes, bencode.DecodedValue], key: bytes, expected_type: type[T]
) -> T:
    value = dictionary.get(key)

    if value is None:
        raise InvalidBencodedData(f"Missing key '{key.decode()}'")

    if not isinstance(value, expected_type):
        raise InvalidBencodedData(
            f"Expected {expected_type.__name__} as value for key '{key.decode()}'"
        )

    return value


def parse_torrent(file: str) -> Torrent:
    with open(file, "rb") as f:
        try:
            decoded = bencode.decode(f.read())
        except bencode.DecodeError as e:
            raise InvalidBencodedData(f"Could not decode torrent file '{file}': {e}")

        if not isinstance(decoded, dict):
            raise InvalidBencodedData(
                "Expected torrent file to contain bencoded dictionary"
            )

        announce = require_type(decoded, ANNOUNCE_KEY, bytes)
        info = require_type(decoded, INFO_KEY, dict)

        name = require_type(info, NAME_KEY, bytes)
        piece_length = require_type(info, PIECE_LEN_KEY, int)
        pieces = require_type(info, PIECES_KEY, bytes)
        length = require_type(info, LENGTH_KEY, int)

        if len(pieces) % SHA1_SIZE != 0:
            raise InvalidBencodedData(
                f"'{PIECES_KEY.decode()}' is not a multiple of {SHA1_SIZE}"
            )

        return Torrent(
            announce=announce,
            info=Info(
                name=name, piece_length=piece_length, pieces=pieces, length=length
            ),
            info_hash=hashlib.sha1(bencode.encode(info)).digest(),
        )


def print_torrent_info(torrent: Torrent) -> None:
    print(f"Tracker URL: {torrent.announce.decode()}")
    print(f"Length: {torrent.info.length}")
    print(f"Info Hash: {torrent.info_hash.hex()}")
    print(f"Piece Length: {torrent.info.piece_length}")
    print("Piece Hashes:")

    for piece in torrent.info.iter_piece_hashes():
        print(piece.hex())


def get_peers(torrent: Torrent) -> list[Peer]:
    tracker_url = torrent.announce.decode()

    r = requests.get(
        tracker_url,
        params={
            "info_hash": torrent.info_hash,
            "peer_id": os.urandom(20),
            "port": 6881,
            "uploaded": 0,
            "downloaded": 0,
            "left": torrent.info.length,
            "compact": 1,
        },
    )

    if r.status_code != requests.codes.ok:
        raise TrackerError(f"Got HTTP code {r.status_code} when requesting peers")

    try:
        decoded = bencode.decode(r.content)
    except bencode.DecodeError:
        raise InvalidBencodedData("Tracker responded with invalid bencoded data")

    if not isinstance(decoded, dict):
        raise InvalidBencodedData("Expected dictionary from tracker")
    if FAILURE_KEY in decoded:
        raise TrackerError(f"Failed with: {decoded[FAILURE_KEY]}")

    p = require_type(decoded, PEERS_KEY, bytes)

    peers = []
    for i in range(0, len(p), 6):
        ip_addr = f"{p[i]}.{p[i + 1]}.{p[i + 2]}.{p[i + 3]}"
        port = int.from_bytes(p[i + 4 : i + 6], byteorder="big")
        peers.append(Peer(ip_addr=ip_addr, port=port))

    return peers


def perform_handshake(conn: socket.socket, info_hash: bytes) -> bytes:
    length = b"\x13"
    protocol = "BitTorrent protocol".encode()
    reserved = b"\x00" * 8
    peer_id = os.urandom(20)

    conn.sendall(b"".join((length, protocol, reserved, info_hash, peer_id)))
    response = conn.recv(68)

    if response[0] != 0x13:
        raise ValueError(f"Invalid handshake response: Expected 0x13 got {response[0]}")
    if response[1:20] != "BitTorrent protocol".encode():
        raise ValueError(
            f"Invalid handshake response: Expected 'BitTorrent protocol' got '{response[1:20]}'"
        )
    # if response[20:28] != b"\x00" * 8:
    #    raise ValueError(
    #        f"Invalid handshake response: Expected '{b'\x00' * 8}', got '{response[20:28]}'"
    #    )
    if response[28:48] != info_hash:
        raise ValueError(
            f"Invalid handshake response: Expected '{info_hash}', got '{response[28:48]}'"
        )

    return response[48:]


def get_bitfield(conn: socket.socket) -> bytes:
    length = int.from_bytes(conn.recv(4), byteorder="big")

    data = conn.recv(length + 1)
    message_id = data[0]

    if message_id != 5:
        raise ValueError(f"Expected bitfield message (type 5) got type {message_id}")

    return data[1:]


def send_interested(conn: socket.socket) -> None:
    conn.sendall(b"\x00\x00\x00\x01\x02")


def get_unchoke(conn: socket.socket) -> None:
    data = conn.recv(5)
    if data[4] != 1:
        raise ValueError(f"Expected unchoke message (type 1) got type {data[4]}")


def get_piece(conn: socket.socket, piece_index: int, piece_length: int) -> bytes:
    piece = bytearray(piece_length)

    for begin in range(0, piece_length, 16 * 1024):
        print(f"Requesting piece {piece_index} offset {begin}, {piece_length=}")
        message_id = b"\x06"  # request
        index = piece_index.to_bytes(4, byteorder="big")
        begin_b = begin.to_bytes(4, byteorder="big")
        block_len = min(16 * 1024, piece_length - begin).to_bytes(4, byteorder="big")
        size = len(message_id) + len(index) + len(begin_b) + len(block_len)
        size = size.to_bytes(4, byteorder="big")

        conn.sendall(b"".join([size, message_id, index, begin_b, block_len]))

        message_len = conn.recv(4)
        message_len = int.from_bytes(message_len, byteorder="big")

        resp = conn.recv(9)
        if resp[0] != 7:
            raise ValueError(f"Expected message_id of 7 (piece) got {resp[0]}")

        index = int.from_bytes(resp[1:5], byteorder="big")
        if index != piece_index:
            raise ValueError(f"Expected piece index {piece_index} but got {index}")

        begin = int.from_bytes(resp[5:9], byteorder="big")

        block = bytearray()
        block_size = message_len - 9
        bytes_recd = 0
        while bytes_recd < block_size:
            chunk = conn.recv(min(block_size - bytes_recd, block_size))
            block += chunk
            bytes_recd += len(chunk)

        piece[begin : begin + block_size] = block

    return piece
