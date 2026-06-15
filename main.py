import bencode
import bittorent
import json
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
    torrent = bittorent.TorrentParser.parse(args.file)
    torrent.print_info()


def peers_cmd(args: argparse.Namespace):
    torrent = bittorent.TorrentParser.parse(args.file)

    for peer in torrent.get_peers():
        print(f"{peer.ip_addr}:{peer.port}")


def handshake_cmd(args: argparse.Namespace):
    torrent = bittorent.TorrentParser.parse(args.file)
    addr = tuple(args.addr.split(":"))

    peer = bittorent.PeerConn(addr, torrent)

    peer_id = peer._perform_handshake()
    print(f"Peer ID: {peer_id.hex()}")

    peer.close()


def download_piece_cmd(args: argparse.Namespace):
    torrent = bittorent.TorrentParser.parse(args.file)
    peers = torrent.get_peers()
    addr = (peers[0].ip_addr, peers[0].port)

    peer = bittorent.PeerConn(addr, torrent)

    peer.prepare()
    piece = peer.get_piece(args.piece_index)
    peer.close()

    with open(args.output, "wb") as f:
        f.write(piece)

def download_cmd(args: argparse.Namespace):
    torrent = bittorent.TorrentParser.parse(args.file)
    peers = torrent.get_peers()

    downloader = bittorent.TorrentDowloader(torrent, peers)
    downloader.download(args.output)
 
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

    # download command
    download = subparsers.add_parser(
        "download",
        help="Dowload single piece from torrent",
    )
    download.add_argument(
        "-o",
        help="name of file to store piece",
        required=True,
        metavar="OUTPUT_FILE",
        dest="output",
    )
    download.add_argument(
        "file",
        help="path to torrent file",
    )
    download.set_defaults(func=download_cmd)

    # parse the args and call whatever function was selected
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
