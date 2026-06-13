from dataclasses import dataclass
from typing import TypeVar, Iterator
from enum import Enum
import bencode
import hashlib
import requests
import os
import socket

SHA1_SIZE = 20
BLOCK_SIZE = 16 * 1024  # 16 KiB

FAILURE_KEY = "failure reason"
INTERVAL_KEY = "interval"
PEERS_KEY = "peers"


class InvalidTorrent(Exception):
    pass


class TrackerError(Exception):
    pass


class PeerError(Exception):
    pass


T = TypeVar("T")


def require_type(
    dictionary: dict[str, bencode.DecodedValue],
    key: str,
    expected_type: type[T],
) -> T:
    value = dictionary.get(key)

    if value is None:
        raise InvalidTorrent(f"Missing key '{key}'")

    if not isinstance(value, expected_type):
        raise InvalidTorrent(
            f"Expected {expected_type.__name__} as value for key '{key}'"
        )

    return value


@dataclass
class Peer:
    # IPv4 addr of peer
    ip_addr: str

    # Port number to conn
    port: int


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
            raise IndexError(
                f"Invalid piece index {index}. Torrent only has {len(self.pieces) // SHA1_SIZE} pieces"
            )
        return min(self.piece_length, self.length - start)


@dataclass
class Torrent:
    # The URL of the tracker
    announce: bytes

    # Info dictionary
    info: Info

    # SHA-1 hash of bencoded info dictionary
    info_hash: bytes

    def print_info(self) -> None:
        print(f"Tracker URL: {self.announce.decode()}")
        print(f"Length: {self.info.length}")
        print(f"Info Hash: {self.info_hash.hex()}")
        print(f"Piece Length: {self.info.piece_length}")
        print("Piece Hashes:")

        for piece in self.info.iter_piece_hashes():
            print(piece.hex())

    def get_peers(self) -> list[Peer]:
        tracker_url = self.announce.decode()

        r = requests.get(
            tracker_url,
            params={
                "info_hash": self.info_hash,
                "peer_id": os.urandom(20),
                "port": 6881,
                "uploaded": 0,
                "downloaded": 0,
                "left": self.info.length,
                "compact": 1,
            },
        )

        if r.status_code != requests.codes.ok:
            raise TrackerError(f"Got HTTP code {r.status_code} when requesting peers")

        try:
            decoded = bencode.decode(r.content)
        except bencode.DecodeError:
            raise TrackerError("Tracker responded with invalid bencoded data")

        if not isinstance(decoded, dict):
            raise TrackerError("Expected dictionary from tracker")

        if FAILURE_KEY in decoded:
            raise TrackerError(f"Failed with: {decoded[FAILURE_KEY]}")

        p = require_type(decoded, PEERS_KEY, bytes)

        peers = []
        for i in range(0, len(p), 6):
            peers.append(
                Peer(
                    ip_addr=f"{p[i]}.{p[i + 1]}.{p[i + 2]}.{p[i + 3]}",
                    port=int.from_bytes(p[i + 4 : i + 6], byteorder="big"),
                )
            )
        return peers


class TorrentParser:
    ANNOUNCE_KEY = "announce"
    INFO_KEY = "info"
    PIECE_LEN_KEY = "piece length"
    PIECES_KEY = "pieces"
    NAME_KEY = "name"
    LENGTH_KEY = "length"

    @staticmethod
    def parse(file: str) -> Torrent:
        with open(file, "rb") as f:
            try:
                decoded = bencode.decode(f.read())
            except bencode.DecodeError as e:
                raise InvalidTorrent(f"Could not parse torrent file '{file}': {e}")

        if not isinstance(decoded, dict):
            raise InvalidTorrent(f"Torrent file '{file}' contains unexpected data")

        announce = require_type(decoded, TorrentParser.ANNOUNCE_KEY, bytes)
        info = require_type(decoded, TorrentParser.INFO_KEY, dict)

        name = require_type(info, TorrentParser.NAME_KEY, bytes)
        piece_length = require_type(info, TorrentParser.PIECE_LEN_KEY, int)
        pieces = require_type(info, TorrentParser.PIECES_KEY, bytes)
        length = require_type(info, TorrentParser.LENGTH_KEY, int)

        if len(pieces) % SHA1_SIZE != 0:
            raise InvalidTorrent(
                f"'{TorrentParser.PIECES_KEY}' is not a multiple of {SHA1_SIZE}"
            )

        return Torrent(
            announce=announce,
            info=Info(
                name=name, piece_length=piece_length, pieces=pieces, length=length
            ),
            info_hash=hashlib.sha1(bencode.encode(info)).digest(),
        )


class PeerMessageID(Enum):
    CHOKE = b"\x00"
    UNCHOKE = b"\x01"
    INTERESTED = b"\x02"
    NOT_INTERESTED = b"\x03"
    HAVE = b"\x04"
    BITFIELD = b"\x05"
    REQUEST = b"\x06"
    PIECE = b"\x07"
    CANCEL = b"\x08"


class PeerConn:
    PROTOCOL: bytes = b"BitTorrent protocol"
    PROTOCOL_LEN: bytes = b"\x13"  # 19
    PROTOCOL_RESERVED: bytes = b"\x00" * 8

    STATE_HANDSHAKE: str = "STATE_HANDSHAKE"
    STATE_BITFIELD: str = "STATE_BITFIELD"
    STATE_CHOKED: str = "STATE_CHOKED"
    STATE_REQUEST: str = "STATE_REQUEST"

    def __init__(self, addr: tuple[str, int], torrent: Torrent):
        self.torrent = torrent
        self.my_id = os.urandom(20)
        self.conn = socket.create_connection(addr)
        self.state = PeerConn.STATE_HANDSHAKE

    def close(self):
        self.conn.close()

    def recv_all(self, expected: int) -> bytes:
        res = bytearray()

        recieved = 0
        while recieved < expected:
            chunk = self.conn.recv(min(expected - recieved, expected))
            if chunk == b"":
                raise PeerError("Peer closed connection")

            res += chunk
            recieved += len(chunk)

        return bytes(res)

    def prepare(self):
        self.peer_id = self._perform_handshake()
        self.bitfield = self._get_bitfield()
        self.send_message(PeerMessageID.INTERESTED)
        self.recv_message(expected=PeerMessageID.UNCHOKE)

    def handshake_msg(self) -> bytes:
        return b"".join(
            [
                PeerConn.PROTOCOL_LEN,
                PeerConn.PROTOCOL,
                PeerConn.PROTOCOL_RESERVED,
                self.torrent.info_hash,
                self.my_id,
            ]
        )

    def _perform_handshake(self) -> bytes:
        if self.state != PeerConn.STATE_HANDSHAKE:
            return self.peer_id

        handshake = self.handshake_msg()
        self.conn.sendall(handshake)
        response = self.recv_all(len(handshake))

        if response[0:1] != PeerConn.PROTOCOL_LEN:
            raise PeerError(
                f"Invalid Protocol Length: expected {PeerConn.PROTOCOL_LEN} got {response[0:1]}"
            )
        if response[1:20] != PeerConn.PROTOCOL:
            raise PeerError(
                f"Invalid Protocol: expected {PeerConn.PROTOCOL} got {response[1:20]}"
            )
        # if response[20:28] != PeerConn.PROTOCOL_RESERVED:
        #     raise PeerError(
        #         f"Invalid Reserverd Bytes: expected {PeerConn.PROTOCOL_RESERVED}, got '{response[20:28]}'"
        #     )
        if response[28:48] != self.torrent.info_hash:
            raise PeerError(
                f"Invalid Info Hash: expected '{self.torrent.info_hash}, got '{response[28:48]}'"
            )

        self.state = PeerConn.STATE_BITFIELD

        return response[48:]

    def _get_bitfield(self) -> bytes:
        if self.state != PeerConn.STATE_BITFIELD:
            return b""

        _, payload = self.recv_message(expected=PeerMessageID.BITFIELD)
        self.state = PeerConn.STATE_CHOKED

        return payload

    def send_message(self, message_id: PeerMessageID, payload: bytes = b"") -> None:
        """
        Peer messages consist of:

        1. message length prefix (4 bytes big-endian)
        2. message id (1 byte)
        3. payload (variable size).

        The length field includes the length of the message id

        """

        length_prefix = (1 + len(payload)).to_bytes(
            length=4,
            byteorder="big",
        )

        msg = b"".join([length_prefix, message_id.value, payload])

        self.conn.sendall(msg)

    def recv_message(
        self, expected: PeerMessageID | None = None
    ) -> tuple[PeerMessageID, bytes]:
        """
        Peer messages consist of:

        1. message length prefix (4 bytes big-endian)
        2. message id (1 byte)
        3. payload (variable size).

        The length field includes the length of the message id

        """
        msg_length = int.from_bytes(
            self.recv_all(4),
            byteorder="big",
        )

        data = self.recv_all(msg_length)

        message_id = PeerMessageID(data[0:1])
        payload = data[1:]

        if expected is not None and message_id != expected:
            raise PeerError(
                f"Expected peer message type {expected.name} but got type {message_id.name}"
            )

        return message_id, payload

    def build_request_msg_payload(
        self, piece_index: int, begin: int, block_len: int
    ) -> bytes:
        return b"".join(
            [
                piece_index.to_bytes(length=4, byteorder="big"),
                begin.to_bytes(length=4, byteorder="big"),
                block_len.to_bytes(length=4, byteorder="big"),
            ]
        )

    def parse_piece_msg_payload(self, payload: bytes) -> tuple[int, int, bytes]:
        index = int.from_bytes(
            payload[0:4],
            byteorder="big",
        )
        begin = int.from_bytes(
            payload[4:8],
            byteorder="big",
        )
        block = payload[8:]

        return index, begin, block

    def get_piece(self, piece_index: int) -> bytes:
        piece_length = self.torrent.info.get_piece_len(piece_index)
        piece = bytearray(piece_length)

        for begin in range(0, piece_length, BLOCK_SIZE):
            block_len = min(BLOCK_SIZE, piece_length - begin)
            payload = self.build_request_msg_payload(piece_index, begin, block_len)

            self.send_message(PeerMessageID.REQUEST, payload=payload)
            _, resp_payload = self.recv_message(expected=PeerMessageID.PIECE)

            index, begin, block = self.parse_piece_msg_payload(resp_payload)

            if index != piece_index:
                raise PeerError(f"Expected piece index {piece_index} but got {index}")

            piece[begin : begin + len(block)] = block

        piece_hash = hashlib.sha1(piece).digest()
        expected_hash = self.torrent.info.get_piece_hash(piece_index)

        if piece_hash != expected_hash:
            raise PeerError(
                f"Piece {piece_index} hash does not match expected:\n\t{piece_hash=}\n\t!=\n\t{expected_hash=}"
            )

        return piece
