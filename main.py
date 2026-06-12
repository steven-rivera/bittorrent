import bencode
import bittorent
import sys
import json
import socket
import hashlib


def main():
    command = sys.argv[1]

    # You can use print statements as follows for debugging, they'll be visible when running tests.
    # print("Logs from your program will appear here!", file=sys.stderr)

    if command == "decode":
        data = sys.argv[2].encode()
        decoded = bencode.decode(data)

        def bytes_to_str(data):
            # json.dumps() can't handle bytes, but bencoded "strings" need to be
            # bytestrings since they might contain non utf-8 characters.

            if isinstance(data, bytes):
                return data.decode()

            raise TypeError(f"Type not serializable: {type(data)}")

        print(json.dumps(decoded, default=bytes_to_str))

    elif command == "info":
        file_name = sys.argv[2]

        torrent = bittorent.parse_torrent(file_name)
        bittorent.print_torrent_info(torrent)

    elif command == "peers":
        file_name = sys.argv[2]

        torrent = bittorent.parse_torrent(file_name)
        peers = bittorent.get_peers(torrent)

        for peer in peers:
            print(f"{peer.ip_addr}:{peer.port}")

    elif command == "handshake":
        file_name = sys.argv[2]
        ip, port = sys.argv[3].split(":")

        torrent = bittorent.parse_torrent(file_name)

        with socket.create_connection((ip, int(port))) as conn:
            peer_id = bittorent.perform_handshake(conn, torrent.info_hash)

            print(f"Peer ID: {peer_id.hex()}")

    elif command == "download_piece":
        piece_file = sys.argv[3]
        file_name = sys.argv[4]
        piece_index = int(sys.argv[5])

        torrent = bittorent.parse_torrent(file_name)
        peers = bittorent.get_peers(torrent)

        ip_addr, port = peers[0].ip_addr, peers[0].port
        with socket.create_connection((ip_addr, port)) as conn:
            peer_id = bittorent.perform_handshake(conn, torrent.info_hash)

            bittorent.get_bitfield(conn)
            bittorent.send_interested(conn)
            bittorent.get_unchoke(conn)

            piece = bittorent.get_piece(conn, piece_index, torrent.info.piece_length)

            piece_hash = hashlib.sha1(piece).digest()
            expected_hash = torrent.info.get_piece_hash(piece_index)

            if piece_hash != expected_hash:
                raise bittorent.DownloadError(
                    f"Piece {piece_index} hash does not match expected:\n\t{piece_hash=}\n\t!=\n\t{expected_hash=}"
                )

            with open(piece_file, "wb") as f:
                f.write(piece)

    else:
        raise NotImplementedError(f"Unknown command {command}")


if __name__ == "__main__":
    main()
