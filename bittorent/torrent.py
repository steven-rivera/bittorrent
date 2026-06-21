import hashlib
import os
from dataclasses import dataclass
from typing import Iterator, TypeVar

import requests

from . import bencode

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

T = TypeVar("T")


class InvalidTorrent(Exception):
    pass


class TrackerError(Exception):
    pass


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

    def num_pieces(self) -> int:
        return len(self.pieces) // SHA1_SIZE

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

    def verify_piece(self, piece_idx: int, piece: bytes) -> bool:
        piece_hash = hashlib.sha1(piece).digest()
        expected_hash = self.info.get_piece_hash(piece_idx)

        return piece_hash == expected_hash


class TorrentParser:
    @staticmethod
    def parse(file: str) -> Torrent:
        with open(file, "rb") as f:
            try:
                decoded = bencode.decode(f.read())
            except bencode.DecodeError as e:
                raise InvalidTorrent(f"Could not parse torrent file '{file}': {e}")

        if not isinstance(decoded, dict):
            raise InvalidTorrent(f"Torrent file '{file}' contains unexpected data")

        announce = require_type(decoded, ANNOUNCE_KEY, bytes)
        info = require_type(decoded, INFO_KEY, dict)

        name = require_type(info, NAME_KEY, bytes)
        piece_length = require_type(info, PIECE_LEN_KEY, int)
        pieces = require_type(info, PIECES_KEY, bytes)
        length = require_type(info, LENGTH_KEY, int)

        if len(pieces) % SHA1_SIZE != 0:
            raise InvalidTorrent(f"'{PIECES_KEY}' is not a multiple of {SHA1_SIZE}")

        return Torrent(
            announce=announce,
            info=Info(
                name=name,
                piece_length=piece_length,
                pieces=pieces,
                length=length,
            ),
            info_hash=hashlib.sha1(
                bencode.encode(info),
            ).digest(),
        )
