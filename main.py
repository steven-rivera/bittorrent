import bencode
import bittorent
import json
import socket
import hashlib
import argparse


def decode_cmd(args: argparse.Namespace):
    data: str = args.data
    decoded = bencode.decode(data.encode())

    def bytes_to_str(data):
        # json.dumps() can't handle bytes, but bencoded "strings" need to be
        # bytestrings since they might contain non utf-8 characters.
        if isinstance(data, bytes):
            return data.decode()

        raise TypeError(f"Type not serializable: {type(data)}")
    
    print(json.dumps(decoded, default=bytes_to_str))


def info_cmd(args: argparse.Namespace):
    torrent = bittorent.parse_torrent(args.file)
    bittorent.print_torrent_info(torrent)


def peers_cmd(args: argparse.Namespace):
    torrent = bittorent.parse_torrent(args.file)
    peers = bittorent.get_peers(torrent)

    for peer in peers:
        print(f"{peer.ip_addr}:{peer.port}")


def handshake_cmd(args: argparse.Namespace):
    torrent = bittorent.parse_torrent(args.file)
    ip, port = args.addr.split(":")

    with socket.create_connection((ip, int(port))) as conn:
        peer_id = bittorent.perform_handshake(conn, torrent.info_hash)
        print(f"Peer ID: {peer_id.hex()}")


def download_piece_cmd(args: argparse.Namespace):
    torrent = bittorent.parse_torrent(args.file)
    peers = bittorent.get_peers(torrent)

    ip_addr, port = peers[0].ip_addr, peers[0].port
    with socket.create_connection((ip_addr, port)) as conn:
        bittorent.perform_handshake(conn, torrent.info_hash)
        bittorent.get_bitfield(conn)
        bittorent.send_interested(conn)
        bittorent.get_unchoke(conn)

        piece = bittorent.get_piece(conn, args.piece_index, torrent.info.get_piece_len(args.piece_index))

        piece_hash = hashlib.sha1(piece).digest()
        expected_hash = torrent.info.get_piece_hash(args.piece_index)

        if piece_hash != expected_hash:
            raise bittorent.DownloadError(
                f"Piece {args.piece_index} hash does not match expected:\n\t{piece_hash=}\n\t!=\n\t{expected_hash=}"
            )

        with open(args.output, "wb") as f:
            f.write(piece)


def main():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    # decode command
    decode = subparsers.add_parser("decode", help="Print bencoded string as JSON")
    decode.add_argument(
        "data",
        help="bencoded string",
    )
    decode.set_defaults(func=decode_cmd)

    # info command
    info = subparsers.add_parser("info", help="Print info about torrent file")
    info.add_argument(
        "file",
        help="path to torrent file",
    )
    info.set_defaults(func=info_cmd)

    # peers command
    peers = subparsers.add_parser(
        "peers", help="Print ip/port for each peer sharing given torrent"
    )
    peers.add_argument(
        "file",
        help="path to torrent file",
    )
    peers.set_defaults(func=peers_cmd)

    # handshake command
    handshake = subparsers.add_parser(
        "handshake",
        help="Perform handshake with peer",
        description="Print the hexadecimal representation of the peer id received during the handshake",
    )
    handshake.add_argument(
        "file",
        help="path to torrent file",
    )
    handshake.add_argument(
        "addr",
        help="<peer_ip>:<peer_port>",
    )
    handshake.set_defaults(func=handshake_cmd)

    # download_piece command
    download_piece = subparsers.add_parser(
        "download_piece",
        help="Dowload single piece from torrent",
    )
    download_piece.add_argument(
        "-o",
        help="name of file to store piece",
        required=True,
        metavar="OUTPUT_FILE",
        dest="output",
    )
    download_piece.add_argument(
        "file",
        help="path to torrent file",
    )
    download_piece.add_argument(
        "piece_index",
        help="index of the piece to download",
        type=int,
    )
    download_piece.set_defaults(func=download_piece_cmd)

    # parse the args and call whatever function was selected
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
