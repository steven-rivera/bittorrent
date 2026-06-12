from dataclasses import dataclass
from typing import TypeVar, Iterator
from enum import Enum
import bencode
import hashlib
import requests
import os
import socket


T = TypeVar("T")
SHA1_SIZE = 20

ANNOUNCE_KEY = "announce"
INFO_KEY = "info"
PIECE_LEN_KEY = "piece length"
PIECES_KEY = "pieces"
NAME_KEY = "name"
LENGTH_KEY = "length"

FAILURE_KEY = "failure reason"
INTERVAL_KEY = "interval"
PEERS_KEY = "peers"


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
    
    def get_piece_len(self, index: int) -> int:
        start = self.piece_length * index
        if start >= self.length:
            raise IndexError(f"Invalid piece index {index}. Torrent only has {len(self.pieces) // SHA1_SIZE} pieces")
        return min(self.piece_length, self.length - start)


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


class PeerMessage(Enum):
    CHOKE = 0
    UNCHOKE = 1
    INTERESTED = 2
    NOT_INTERESTED = 3
    HAVE = 4
    BITFIELD = 5
    REQUEST = 6
    PIECE = 7
    CANCEL = 8


def require_type(
    dictionary: dict[str, bencode.DecodedValue], key: str, expected_type: type[T]
) -> T:
    value = dictionary.get(key)

    if value is None:
        raise InvalidBencodedData(f"Missing key '{key}'")

    if not isinstance(value, expected_type):
        raise InvalidBencodedData(
            f"Expected {expected_type.__name__} as value for key '{key}'"
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
                f"'{PIECES_KEY}' is not a multiple of {SHA1_SIZE}"
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


def recv_all(conn: socket.socket, expected: int) -> bytes:
    res = bytearray()

    recieved = 0
    while recieved < expected:
        chunk = conn.recv(min(expected - recieved, expected))
        if chunk == b'':
            raise DownloadError("Peer closed connection")

        res += chunk
        recieved += len(chunk)

    return bytes(res)


def perform_handshake(conn: socket.socket, info_hash: bytes) -> bytes:
    length = b"\x13"
    protocol = "BitTorrent protocol".encode()
    reserved = b"\x00" * 8
    peer_id = os.urandom(20)

    handshake = b"".join((length, protocol, reserved, info_hash, peer_id))
    conn.sendall(handshake)

    response = recv_all(conn, len(handshake))

    if response[0] != 0x13:
        raise DownloadError(
            f"Invalid handshake response: Expected 0x13 got {response[0]}"
        )
    if response[1:20] != "BitTorrent protocol".encode():
        raise DownloadError(
            f"Invalid handshake response: Expected 'BitTorrent protocol' got '{response[1:20]}'"
        )
    # if response[20:28] != b"\x00" * 8:
    #    raise DownloadError(
    #        f"Invalid handshake response: Expected '{b'\x00' * 8}', got '{response[20:28]}'"
    #    )
    if response[28:48] != info_hash:
        raise DownloadError(
            f"Invalid handshake response: Expected '{info_hash}', got '{response[28:48]}'"
        )

    # peer_id
    return response[48:]


# +-------------------+
# | length (4 bytes)  |
# +-------------------+
# | message id (1 B)  |
# +-------------------+
# | payload (n bytes) |
# +-------------------+

# The length field includes both the length of the message ID (1 byte).


def get_bitfield(conn: socket.socket) -> bytes:
    length = int.from_bytes(recv_all(conn, 4), byteorder="big")

    data = recv_all(conn, length)
    message_id = data[0]

    if message_id != PeerMessage.BITFIELD.value:
        raise DownloadError(
            f"Expected {PeerMessage.BITFIELD.name} peer message but got type {message_id}"
        )

    return data[1:]


def send_interested(conn: socket.socket) -> None:
    length_prefix = (1).to_bytes(length=4, byteorder="big")
    message_id = PeerMessage.INTERESTED.value.to_bytes(length=1)

    conn.sendall(length_prefix + message_id)


def get_unchoke(conn: socket.socket) -> None:
    length = int.from_bytes(recv_all(conn, 4), byteorder="big")

    data = recv_all(conn, length)
    message_id = data[0]

    if message_id != PeerMessage.UNCHOKE.value:
        raise DownloadError(
            f"Expected {PeerMessage.UNCHOKE.name} peer message got type {message_id}"
        )


def get_piece(conn: socket.socket, piece_index: int, piece_length: int) -> bytes:
    piece = bytearray(piece_length)
    piece_index_b = piece_index.to_bytes(length=4, byteorder="big")

    for begin in range(0, piece_length, 16 * 1024):        
        message_id_b = PeerMessage.REQUEST.value.to_bytes(length=1)
        begin_b = begin.to_bytes(length=4, byteorder="big")
        block_len_b = min(16 * 1024, piece_length - begin).to_bytes(
            length=4, byteorder="big"
        )

        msg_len = len(message_id_b) + len(piece_index_b) + len(begin_b) + len(block_len_b)
        msg_len_b = msg_len.to_bytes(4, byteorder="big")

        conn.sendall(
            b"".join([msg_len_b, message_id_b, piece_index_b, begin_b, block_len_b])
        )

        message_len = int.from_bytes(recv_all(conn, 4), byteorder="big")
        resp = recv_all(conn, message_len)

        message_id = resp[0]
        if message_id != PeerMessage.PIECE.value:
            raise DownloadError(
                f"Expected {PeerMessage.PIECE.name} peer message but got type {message_id}"
            )
        piece_index_resp = int.from_bytes(resp[1:5], byteorder="big")
        if piece_index_resp != piece_index:
            raise DownloadError(
                f"Expected piece index {piece_index} but got {piece_index_resp}"
            )

        begin = int.from_bytes(resp[5:9], byteorder="big")
        block = resp[9:]
        piece[begin : begin + len(block)] = block

    return piece
