"""Bounded structural checks for the newly supported GIF attachment type."""


def is_complete_gif(data: bytes) -> bool:
    # Check the container without decoding pixels or adding a native decoder.
    # MIME alone or a six-byte magic string is not sufficient to accept a file.
    if len(data) < 14 or data[:6] not in (b"GIF87a", b"GIF89a"):
        return False
    width = int.from_bytes(data[6:8], "little")
    height = int.from_bytes(data[8:10], "little")
    if not width or not height or width * height > 64_000_000:
        return False
    packed = data[10]
    position = 13 + (3 * (1 << ((packed & 7) + 1)) if packed & 128 else 0)
    if position >= len(data):
        return False
    seen_image = False

    def sub_blocks(start: int, *, require_data: bool = False) -> int | None:
        position = start
        has_data = False
        while position < len(data):
            size = data[position]
            position += 1
            if size == 0:
                return position if has_data or not require_data else None
            has_data = True
            position += size
            if position > len(data):
                return None
        return None

    while position < len(data):
        marker = data[position]
        position += 1
        if marker == 0x3B:
            return seen_image and position == len(data)
        if marker == 0x21:
            if position >= len(data):
                return False
            # The extension label is followed by length-prefixed sub-blocks.
            position = sub_blocks(position + 1)
        elif marker == 0x2C:
            if position + 9 > len(data):
                return False
            image_width = int.from_bytes(data[position + 4:position + 6], "little")
            image_height = int.from_bytes(data[position + 6:position + 8], "little")
            if not image_width or not image_height or image_width * image_height > 64_000_000:
                return False
            packed = data[position + 8]
            position += 9
            if packed & 128:
                position += 3 * (1 << ((packed & 7) + 1))
            if position >= len(data) or not 2 <= data[position] <= 8:
                return False
            position = sub_blocks(position + 1, require_data=True)
            seen_image = True
        else:
            return False
        if position is None:
            return False
    return False
