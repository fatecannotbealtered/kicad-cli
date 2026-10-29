"""Read only the documented exception/context stream of a local AMD64 minidump."""

import struct
from pathlib import Path


def exception(path):
    with Path(path).open("rb") as stream:
        header = stream.read(32)
        if header[:4] != b"MDMP":
            raise ValueError("Not a minidump")
        count, directory = struct.unpack_from("<II", header, 8)
        if count > 10000:
            raise ValueError("Oversized directory")
        stream.seek(directory)
        entries = [struct.unpack("<III", stream.read(12)) for _ in range(count)]
        _, size, offset = next(row for row in entries if row[0] == 6)
        if size != 168:
            raise ValueError("Unsupported exception stream")
        stream.seek(offset)
        data = stream.read(size)
        thread, code, flags = struct.unpack_from("<I", data)[0], *struct.unpack_from("<II", data, 8)
        address = struct.unpack_from("<Q", data, 24)[0]
        number = struct.unpack_from("<I", data, 32)[0]
        if number > 15:
            raise ValueError("Invalid exception parameters")
        parameters = list(struct.unpack_from("<" + "Q" * number, data, 40))
        context_size, context_offset = struct.unpack_from("<II", data, 160)
        if context_size < 1232:
            raise ValueError("No supported AMD64 context")
        stream.seek(context_offset)
        context = stream.read(1232)
        context_flags = struct.unpack_from("<I", context, 48)[0]
        if not context_flags & 0x100000:
            raise ValueError("Not an AMD64 context")
        return {
            "thread_id": thread,
            "code": hex(code),
            "flags": flags,
            "address": hex(address),
            "parameters": parameters,
            "rip": hex(struct.unpack_from("<Q", context, 248)[0]),
            "rsp": hex(struct.unpack_from("<Q", context, 152)[0]),
            "context_flags": hex(context_flags),
        }
