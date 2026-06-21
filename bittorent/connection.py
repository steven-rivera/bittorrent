import os
import select
import socket
from enum import Enum
from queue import Empty, Queue
from threading import Event, Semaphore, Thread

from .debug import DEBUG, blue, green, grey, red, yellow
from .torrent import Torrent

BLOCK_SIZE = 16 * 1024  # 16 KiB
MAX_PENDING = 5

PROTOCOL = b"BitTorrent protocol"
PROTOCOL_LEN = b"\x13"  # 19
PROTOCOL_RESERVED = b"\x00\x00\x00\x00\x00\x00\x00\x00"


class ConnectionClosedError(Exception):
    pass


class HandshakeError(Exception):
    pass


class Message(Enum):
    CHOKE = b"\x00"
    UNCHOKE = b"\x01"
    INTERESTED = b"\x02"
    NOT_INTERESTED = b"\x03"
    HAVE = b"\x04"
    BITFIELD = b"\x05"
    REQUEST = b"\x06"
    PIECE = b"\x07"
    CANCEL = b"\x08"


class PeerConnection:
    def __init__(self, sock: socket.socket, torrent: Torrent):
        self.sock = sock
        self.torrent = torrent
        self.id = os.urandom(20)

        self.pending = Semaphore(value=MAX_PENDING)
        self.outgoing_msgs: Queue[bytes] = Queue()
        self.received_bitfield = Event()
        self.is_unchoked = Event()

        self.reader = Thread(target=self.reader_loop, daemon=True)
        self.writer = Thread(target=self.writer_loop, daemon=True)
        self.is_closed = Event()

    def start(self):
        self.reader.start()
        self.writer.start()

    def join(self):
        self.writer.join()
        self.reader.join()

    def close(self):
        self.sock.close()
        self.is_closed.set()

    def handshake(self) -> bytes:
        handshake = b"".join(
            [
                PROTOCOL_LEN,
                PROTOCOL,
                PROTOCOL_RESERVED,
                self.torrent.info_hash,
                self.id,
            ]
        )

        if DEBUG:
            print(yellow(f"{id(self)}: Sending handshake"))

        self.sock.sendall(handshake)
        response = self.recv_all(len(handshake))

        len_resp = response[0:1]
        proto_resp = response[1:20]
        hash_resp = response[28:48]

        if (
            len_resp != PROTOCOL_LEN
            or proto_resp != PROTOCOL
            or hash_resp != self.torrent.info_hash
        ):
            raise HandshakeError(f"Unexpected handshake response: {response}")

        if DEBUG:
            print(green(f"{id(self)}: Received valid handshake"))

        self.peer_id = response[48:]

        return self.peer_id

    def send_message(self, msg: bytes):
        self.outgoing_msgs.put(msg)

    def download_piece(self, piece_idx: int) -> bytes:
        if not self.is_unchoked.is_set():
            self.is_unchoked.wait()

        piece_len = self.torrent.info.get_piece_len(piece_idx)

        self.piece = bytearray(piece_len)
        self.pending_requests = Queue()

        for begin in range(0, piece_len, BLOCK_SIZE):
            block_len = min(BLOCK_SIZE, piece_len - begin)

            msg = build_peer_message(
                Message.REQUEST,
                payload=build_request_message_payload(
                    piece_idx=piece_idx,
                    begin=begin,
                    block_len=block_len,
                ),
            )

            self.pending.acquire()
            self.pending_requests.put(begin)
            self.send_message(msg)

            if DEBUG:
                print(
                    grey(
                        f"\t{id(self)}: Req piece {piece_idx}: {begin=} len={block_len}"
                    )
                )

        self.pending_requests.join()

        return self.piece

    def recv_all(self, expected: int) -> bytes:
        res = bytearray()

        recieved = 0
        while recieved < expected:
            chunk = self.sock.recv(min(expected - recieved, expected))
            if chunk == b"":
                raise ConnectionClosedError("Peer closed connection")

            res += chunk
            recieved += len(chunk)

        return bytes(res)

    def recv_peer_message(self) -> tuple[Message, bytes]:
        msg_length = int.from_bytes(
            self.recv_all(4),
            byteorder="big",
        )

        data = self.recv_all(msg_length)

        message_id = Message(data[0:1])
        payload = data[1:]

        return message_id, payload

    def has_piece(self, piece_idx: int) -> bool:
        if not self.received_bitfield.is_set():
            self.received_bitfield.wait()

        return True

    def writer_loop(self):
        while True:
            try:
                msg = self.outgoing_msgs.get(timeout=1)
            except Empty:
                if self.is_closed.is_set():
                    break
            else:
                try:
                    self.sock.sendall(msg)
                except Exception as e:
                    print(red(f"{id(self)}: Writer: {e}"))
                    break

        if DEBUG:
            print(grey(f"{id(self)}: Exiting writer thread"))

    def reader_loop(self):
        while True:
            ready, _, _ = select.select([self.sock], [], [], 1.0)

            if self.is_closed.is_set():
                break

            if not ready:
                continue

            try:
                message_id, resp_payload = self.recv_peer_message()
            except Exception:
                break

            if message_id == Message.PIECE:
                index, begin, block = parse_piece_message_payload(resp_payload)

                if DEBUG:
                    print(
                        blue(
                            f"\t{id(self)}: Rec piece {index}: {begin=} len={len(block)}"
                        )
                    )

                self.piece[begin : begin + len(block)] = block
                self.pending_requests.task_done()
                self.pending.release()

            elif message_id == Message.BITFIELD:
                if DEBUG:
                    print(green(f"{id(self)}: Recieved bitfield message"))

                self.bitfield = resp_payload
                self.received_bitfield.set()

                self.send_message(build_peer_message(Message.INTERESTED))

                if DEBUG:
                    print(yellow(f"{id(self)}: Sending interested message"))

            elif message_id == Message.UNCHOKE:
                self.is_unchoked.set()

                if DEBUG:
                    print(green(f"{id(self)}: Recieved unchoke message"))

        if DEBUG:
            print(grey(f"{id(self)}: Exiting reader thread"))


def build_peer_message(message_id: Message, payload: bytes = b"") -> bytes:
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

    return b"".join([length_prefix, message_id.value, payload])


def build_request_message_payload(piece_idx: int, begin: int, block_len: int) -> bytes:
    return b"".join(
        [
            piece_idx.to_bytes(length=4, byteorder="big"),
            begin.to_bytes(length=4, byteorder="big"),
            block_len.to_bytes(length=4, byteorder="big"),
        ]
    )


def parse_piece_message_payload(payload: bytes) -> tuple[int, int, bytes]:
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
