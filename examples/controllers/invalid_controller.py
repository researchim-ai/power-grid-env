"""Пишет ответ вне схемы действия."""

import json
import sys


def main() -> None:
    for line in sys.stdin:
        message = json.loads(line)
        if message.get("op") == "act":
            sys.stdout.write("this is not json\n")
            sys.stdout.flush()
        elif message.get("op") == "close":
            return
        else:
            sys.stdout.write(json.dumps({"ok": True}) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
