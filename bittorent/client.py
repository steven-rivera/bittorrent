import concurrent.futures
import socket
import threading
import time
from threading import Lock

from .debug import DEBUG, green, grey, red, yellow
from .connection import PeerConnection
from .torrent import Peer, Torrent

MAX_PEERS = 3


class TorrentClient:
    def __init__(
        self,
        torrent: Torrent,
        output: str = "torrent_download",
        listen_port: int = 6881,
    ):
        self.torrent = torrent
        self.output_file = output
        self.listen_port = listen_port

        self.max_connections = threading.Semaphore(value=MAX_PEERS)

    def download(self):
        peers: list[Peer] = self.torrent.get_peers()

        self.scheduler = PieceScheduler(self.torrent.info.num_pieces())
        self.store = PieceStore(self.output_file, self.torrent.info.piece_length)

        with concurrent.futures.ThreadPoolExecutor() as executor:
            for peer in peers:
                self.max_connections.acquire()

                if self.scheduler.complete():
                    break

                try:
                    peer_conn = PeerConnection(
                        sock=socket.create_connection(
                            (peer.ip_addr, peer.port), timeout=5
                        ),
                        torrent=self.torrent,
                    )
                except TimeoutError:
                    if DEBUG:
                        print(red(f"Couldn't connect to peer {peer}"))
                    self.max_connections.release()
                else:
                    executor.submit(self.download_worker, peer_conn)

        if DEBUG:
            print(green("Finised download"))

    def download_worker(self, conn: PeerConnection):
        conn.handshake()
        conn.start()

        while not self.scheduler.complete():
            piece_idx = self.scheduler.acquire_piece()
            if piece_idx is None:
                time.sleep(0.1)
                continue

            if not conn.has_piece(piece_idx):
                self.scheduler.piece_failed(piece_idx)
                continue

            if DEBUG:
                print(yellow(f"{id(conn)}: Started piece {piece_idx}"))

            piece = conn.download_piece(piece_idx)

            if DEBUG:
                print(green(f"{id(conn)}: Finished piece {piece_idx}"))

            if not self.torrent.verify_piece(piece_idx, piece):
                self.scheduler.piece_failed(piece_idx)

                if DEBUG:
                    print(red(f"{id(conn)}: piece {piece_idx} invalid hash"))

                continue

            self.scheduler.piece_finished(piece_idx)
            self.store.write_piece(piece_idx, piece)

        conn.close()
        conn.join()

        if DEBUG:
            print(grey(f"{id(conn)}: Threads terminated"))

        self.max_connections.release()


class PieceStore:
    def __init__(self, output_file: str, piece_length: int):
        self.file = open(output_file, "w+b")
        self.piece_length = piece_length
        self.completed: set[int] = set()
        self.lock = Lock()

    def has_piece(self, piece_idx: int) -> bool:
        return piece_idx in self.completed

    def write_piece(self, piece_idx: int, piece: bytes):
        offset = piece_idx * self.piece_length

        with self.lock:
            self.file.seek(offset)
            self.file.write(piece)

        self.completed.add(piece_idx)

    def read_piece(self, piece_idx: int) -> bytes:
        offset = piece_idx * self.piece_length

        with self.lock:
            self.file.seek(offset)
            return self.file.read(self.piece_length)


class PieceScheduler:
    def __init__(self, num_pieces: int):
        self.remaining: set[int] = set(range(num_pieces))
        self.in_progress: set[int] = set()
        self.completed: set[int] = set()
        self.lock = Lock()

    def acquire_piece(self) -> int | None:
        with self.lock:
            if len(self.remaining) == 0:
                return None

            piece_idx = self.remaining.pop()
            self.in_progress.add(piece_idx)

            return piece_idx

    def piece_finished(self, piece_idx: int):
        with self.lock:
            self.in_progress.remove(piece_idx)
            self.completed.add(piece_idx)

    def piece_failed(self, piece_idx: int):
        with self.lock:
            self.in_progress.remove(piece_idx)
            self.remaining.add(piece_idx)

    def complete(self) -> bool:
        with self.lock:
            return len(self.remaining) == 0 and len(self.in_progress) == 0
