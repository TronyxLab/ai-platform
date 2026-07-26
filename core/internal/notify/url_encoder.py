#!/usr/bin/env python3
# GREP_SUMMARY: url_encoder.py, urllib, urlencode, telegram
# STRUCTURE: ▶ url_encode → ⎋ CLI (argparse: encode)
"""
URL-encode text for Telegram bot API.

Usage:
    python3 url_encoder.py encode <text>

Outputs the URL-encoded text to stdout.
"""

import argparse
import urllib.parse


def url_encode(text: str) -> str:
    """URL-encode a string using urllib.parse.quote with safe=''.

    Args:
        text: String to encode

    Returns:
        URL-encoded string
    """
    return urllib.parse.quote(text, safe="")


def main() -> None:
    parser = argparse.ArgumentParser(description="URL-encode text for Telegram bot API")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.required = True

    encode_parser = subparsers.add_parser("encode", help="URL-encode text")
    encode_parser.add_argument("text", nargs="+", help="Text to encode")

    args = parser.parse_args()

    if args.command == "encode":
        text = " ".join(args.text)
        print(url_encode(text))


if __name__ == "__main__":
    main()
