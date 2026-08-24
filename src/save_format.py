"""Minimal Grim Dawn ``player.gdc`` container reader and writer.

The save format uses a stateful byte cipher.  Most top-level blocks may be
preserved as opaque plaintext, but blocks 3 and 4 contain nested blocks whose
length/checksum words are deliberately not encrypted.  Those two blocks are
therefore represented explicitly so a renamed header can be followed by a
correctly re-encrypted remainder of the file.

The format details are based on the public gd-edit implementation by Jonathan
Shieh (EPL-1.0), with the container handling kept deliberately version-neutral
for current Grim Dawn 1.3 saves.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import io
import struct
from pathlib import Path
from typing import BinaryIO


UINT32_MASK = 0xFFFFFFFF
SEED_XOR = 0x55555555


class SaveFormatError(ValueError):
    """Raised when a save is truncated, corrupt, or structurally unsupported."""


def _u32(value: int) -> int:
    return value & UINT32_MASK


def _read_exact(stream: BinaryIO, size: int) -> bytes:
    data = stream.read(size)
    if len(data) != size:
        raise SaveFormatError(
            f"存档数据提前结束：需要 {size} 字节，实际只有 {len(data)} 字节"
        )
    return data


def _raw_u32(stream: BinaryIO) -> int:
    return struct.unpack("<I", _read_exact(stream, 4))[0]


def _pack_u32(value: int) -> bytes:
    return struct.pack("<I", _u32(value))


def generate_encryption_table(seed: int) -> tuple[int, ...]:
    table: list[int] = []
    value = _u32(seed)
    for _ in range(256):
        rotated = _u32((value << 31) | (value >> 1))
        value = _u32(rotated * 39916801)
        table.append(value)
    return tuple(table)


class Cipher:
    def __init__(self, seed: int):
        self.state = _u32(seed)
        self.table = generate_encryption_table(seed)

    def _advance(self, encrypted_byte: int) -> None:
        self.state = _u32(self.state ^ self.table[encrypted_byte])

    def decrypt(self, encrypted: bytes) -> bytes:
        result = bytearray(len(encrypted))
        for index, encrypted_byte in enumerate(encrypted):
            result[index] = encrypted_byte ^ (self.state & 0xFF)
            self._advance(encrypted_byte)
        return bytes(result)

    def encrypt(self, plaintext: bytes) -> bytes:
        result = bytearray(len(plaintext))
        for index, plain_byte in enumerate(plaintext):
            encrypted_byte = plain_byte ^ (self.state & 0xFF)
            result[index] = encrypted_byte
            self._advance(encrypted_byte)
        return bytes(result)

    def read_bytes(self, stream: BinaryIO, size: int) -> bytes:
        return self.decrypt(_read_exact(stream, size))

    def read_u32(self, stream: BinaryIO) -> int:
        encrypted = _read_exact(stream, 4)
        encrypted_value = struct.unpack("<I", encrypted)[0]
        value = _u32(encrypted_value ^ self.state)
        for byte in encrypted:
            self._advance(byte)
        return value

    def write_bytes(self, stream: BinaryIO, plaintext: bytes) -> None:
        stream.write(self.encrypt(plaintext))

    def write_u32(self, stream: BinaryIO, value: int) -> None:
        encrypted_value = _u32(value ^ self.state)
        encrypted = _pack_u32(encrypted_value)
        stream.write(encrypted)
        for byte in encrypted:
            self._advance(byte)


@dataclass
class Header:
    magic: int
    file_version: int
    character_name: str
    male: bool
    player_class_name: str
    character_level: int
    hardcore: bool
    expansion_character: int


@dataclass
class NestedBlock:
    block_id: int
    payload: object


@dataclass
class Block3Data:
    version: int
    has_data: bool
    sack_count: int = 0
    focused_sack: int = 0
    selected_sack: int = 0
    sacks: list[NestedBlock] = field(default_factory=list)
    tail: object = None


@dataclass
class Block4Data:
    version: int
    stashes: list[NestedBlock] = field(default_factory=list)
    tail: object = None


@dataclass
class SaveBlock:
    block_id: int
    payload: object


@dataclass
class CharacterSave:
    seed: int
    header: Header
    data_version: int
    mystery_field: bytes
    blocks: list[SaveBlock]

    @classmethod
    def load(cls, path: str | Path) -> "CharacterSave":
        return cls.from_bytes(Path(path).read_bytes())

    @classmethod
    def from_bytes(cls, data: bytes) -> "CharacterSave":
        stream = io.BytesIO(data)
        encoded_seed = _raw_u32(stream)
        seed = _u32(encoded_seed ^ SEED_XOR)
        cipher = Cipher(seed)

        header = Header(
            magic=cipher.read_u32(stream),
            file_version=cipher.read_u32(stream),
            character_name=_read_encrypted_string(stream, cipher, "utf-16-le"),
            male=bool(cipher.read_bytes(stream, 1)[0]),
            player_class_name=_read_encrypted_string(stream, cipher, "ascii"),
            character_level=cipher.read_u32(stream),
            hardcore=bool(cipher.read_bytes(stream, 1)[0]),
            expansion_character=cipher.read_bytes(stream, 1)[0],
        )

        if _magic_text(header.magic) != "GDCX":
            raise SaveFormatError(
                f"不是有效的 player.gdc：文件标记为 {_magic_text(header.magic)!r}"
            )

        _verify_checksum(stream, cipher, "角色头")
        data_version = cipher.read_u32(stream)
        mystery_field = cipher.read_bytes(stream, 16)

        blocks: list[SaveBlock] = []
        while stream.tell() < len(data):
            block_id = cipher.read_u32(stream)
            state_at_length = cipher.state
            length = _raw_u32(stream) ^ state_at_length
            payload_end = stream.tell() + length
            if payload_end > len(data):
                raise SaveFormatError(
                    f"数据块 {block_id} 长度越界：{length} 字节"
                )

            payload = _read_block_payload(block_id, stream, cipher, payload_end)

            if stream.tell() != payload_end:
                raise SaveFormatError(
                    f"数据块 {block_id} 解析位置不一致："
                    f"期望 {payload_end}，实际 {stream.tell()}"
                )
            _verify_checksum(stream, cipher, f"数据块 {block_id}")
            blocks.append(SaveBlock(block_id, payload))

        return cls(seed, header, data_version, mystery_field, blocks)

    def block(self, block_id: int) -> SaveBlock:
        for block in self.blocks:
            if block.block_id == block_id:
                return block
        raise SaveFormatError(f"模板存档缺少数据块 {block_id}")

    def to_bytes(self) -> bytes:
        stream = io.BytesIO()
        stream.write(_pack_u32(self.seed ^ SEED_XOR))
        cipher = Cipher(self.seed)

        cipher.write_u32(stream, self.header.magic)
        cipher.write_u32(stream, self.header.file_version)
        _write_encrypted_string(
            stream, cipher, self.header.character_name, "utf-16-le"
        )
        cipher.write_bytes(stream, bytes((1 if self.header.male else 0,)))
        _write_encrypted_string(
            stream, cipher, self.header.player_class_name, "ascii"
        )
        cipher.write_u32(stream, self.header.character_level)
        cipher.write_bytes(stream, bytes((1 if self.header.hardcore else 0,)))
        cipher.write_bytes(stream, bytes((self.header.expansion_character,)))
        stream.write(_pack_u32(cipher.state))

        cipher.write_u32(stream, self.data_version)
        cipher.write_bytes(stream, self.mystery_field)

        for block in self.blocks:
            _write_top_block(stream, cipher, block)
        return stream.getvalue()

    def write(self, path: str | Path) -> None:
        Path(path).write_bytes(self.to_bytes())


def _magic_text(value: int) -> str:
    return _pack_u32(value).decode("ascii", errors="replace")


def _read_encrypted_string(
    stream: BinaryIO, cipher: Cipher, encoding: str
) -> str:
    character_count = cipher.read_u32(stream)
    byte_count = character_count * (2 if encoding == "utf-16-le" else 1)
    return cipher.read_bytes(stream, byte_count).decode(encoding)


def _write_encrypted_string(
    stream: BinaryIO, cipher: Cipher, value: str, encoding: str
) -> None:
    encoded = value.encode(encoding)
    character_count = len(encoded) // (2 if encoding == "utf-16-le" else 1)
    cipher.write_u32(stream, character_count)
    cipher.write_bytes(stream, encoded)


def _verify_checksum(stream: BinaryIO, cipher: Cipher, description: str) -> None:
    checksum = _raw_u32(stream)
    if checksum != cipher.state:
        raise SaveFormatError(
            f"{description} 校验失败：文件为 {checksum:08X}，"
            f"计算值为 {cipher.state:08X}"
        )


def _read_nested_block(
    stream: BinaryIO, cipher: Cipher, payload_reader
) -> NestedBlock:
    block_id = cipher.read_u32(stream)
    length = _raw_u32(stream) ^ cipher.state
    payload_end = stream.tell() + length
    payload = payload_reader(stream, cipher)
    if stream.tell() != payload_end:
        raise SaveFormatError(
            f"嵌套数据块 {block_id} 长度不匹配："
            f"期望结束于 {payload_end}，实际 {stream.tell()}"
        )
    _verify_checksum(stream, cipher, f"嵌套数据块 {block_id}")
    return NestedBlock(block_id, payload)


def _read_block3(
    stream: BinaryIO, cipher: Cipher, payload_end: int
) -> Block3Data:
    version = cipher.read_u32(stream)
    has_data = bool(cipher.read_bytes(stream, 1)[0])
    if not has_data:
        if stream.tell() != payload_end:
            raise SaveFormatError("无背包数据的数据块 3 含有未识别字段")
        return Block3Data(version=version, has_data=False)

    sack_count = cipher.read_u32(stream)
    focused_sack = cipher.read_u32(stream)
    selected_sack = cipher.read_u32(stream)
    has_ascendant_fields = version >= 11
    sacks = [
        _read_nested_block(
            stream,
            cipher,
            lambda s, c: _read_inventory_sack(s, c, has_ascendant_fields),
        )
        for _ in range(sack_count)
    ]
    tail = _read_block3_tail(stream, cipher, has_ascendant_fields)
    return Block3Data(
        version=version,
        has_data=True,
        sack_count=sack_count,
        focused_sack=focused_sack,
        selected_sack=selected_sack,
        sacks=sacks,
        tail=tail,
    )


def _read_block4(
    stream: BinaryIO, cipher: Cipher, payload_end: int
) -> Block4Data:
    version = cipher.read_u32(stream)
    stash_count = cipher.read_u32(stream)
    has_ascendant_fields = version >= 5
    stashes = [
        _read_nested_block(
            stream,
            cipher,
            lambda s, c: _read_stash(s, c, has_ascendant_fields),
        )
        for _ in range(stash_count)
    ]
    tail = b""
    return Block4Data(version=version, stashes=stashes, tail=tail)


def _write_nested_block(
    stream: io.BytesIO, cipher: Cipher, block: NestedBlock, payload_writer
) -> None:
    cipher.write_u32(stream, block.block_id)
    state_at_length = cipher.state
    length_position = stream.tell()
    stream.write(b"\0\0\0\0")
    payload_start = stream.tell()
    payload_writer(stream, cipher, block.payload)
    payload_end = stream.tell()
    current_position = stream.tell()
    stream.seek(length_position)
    stream.write(_pack_u32((payload_end - payload_start) ^ state_at_length))
    stream.seek(current_position)
    stream.write(_pack_u32(cipher.state))


def _write_top_block(
    stream: io.BytesIO, cipher: Cipher, block: SaveBlock
) -> None:
    cipher.write_u32(stream, block.block_id)
    state_at_length = cipher.state
    length_position = stream.tell()
    stream.write(b"\0\0\0\0")
    payload_start = stream.tell()

    if isinstance(block.payload, Block3Data):
        payload = block.payload
        cipher.write_u32(stream, payload.version)
        cipher.write_bytes(stream, bytes((1 if payload.has_data else 0,)))
        if payload.has_data:
            if payload.sack_count != len(payload.sacks):
                raise SaveFormatError("数据块 3 的背包数量与内容不一致")
            cipher.write_u32(stream, payload.sack_count)
            cipher.write_u32(stream, payload.focused_sack)
            cipher.write_u32(stream, payload.selected_sack)
            for nested in payload.sacks:
                _write_nested_block(
                    stream,
                    cipher,
                    nested,
                    lambda s, c, v: _write_inventory_sack(
                        s, c, v, payload.version >= 11
                    ),
                )
            _write_block3_tail(
                stream, cipher, payload.tail, payload.version >= 11
            )
    elif isinstance(block.payload, Block4Data):
        payload = block.payload
        cipher.write_u32(stream, payload.version)
        cipher.write_u32(stream, len(payload.stashes))
        for nested in payload.stashes:
            _write_nested_block(
                stream,
                cipher,
                nested,
                lambda s, c, v: _write_stash(
                    s, c, v, payload.version >= 5
                ),
            )
    else:
        _write_block_payload(stream, cipher, block.block_id, block.payload)

    payload_end = stream.tell()
    payload_length = payload_end - payload_start
    current_position = stream.tell()
    stream.seek(length_position)
    stream.write(_pack_u32(payload_length ^ state_at_length))
    stream.seek(current_position)
    stream.write(_pack_u32(cipher.state))


class PlainReader:
    """Little-endian reader used inside already decrypted block payloads."""

    def __init__(self, data: bytes):
        self.stream = io.BytesIO(data)
        self.size = len(data)

    @property
    def position(self) -> int:
        return self.stream.tell()

    @property
    def remaining(self) -> int:
        return self.size - self.position

    def bytes(self, size: int) -> bytes:
        return _read_exact(self.stream, size)

    def u8(self) -> int:
        return self.bytes(1)[0]

    def boolean(self) -> bool:
        value = self.u8()
        if value not in (0, 1):
            raise SaveFormatError(f"无效布尔值 {value}，位置 {self.position - 1}")
        return bool(value)

    def u32(self) -> int:
        return struct.unpack("<I", self.bytes(4))[0]

    def i32(self) -> int:
        return struct.unpack("<i", self.bytes(4))[0]

    def f32(self) -> float:
        return struct.unpack("<f", self.bytes(4))[0]

    def string(self, encoding: str = "ascii", static_length: int | None = None) -> str:
        count = static_length if static_length is not None else self.u32()
        byte_count = count * (2 if encoding == "utf-16-le" else 1)
        return self.bytes(byte_count).decode(encoding)


class PlainWriter:
    def __init__(self):
        self.stream = io.BytesIO()

    def getvalue(self) -> bytes:
        return self.stream.getvalue()

    def bytes(self, value: bytes) -> None:
        self.stream.write(value)

    def u8(self, value: int) -> None:
        self.stream.write(bytes((value & 0xFF,)))

    def boolean(self, value: bool) -> None:
        self.u8(1 if value else 0)

    def u32(self, value: int) -> None:
        self.stream.write(_pack_u32(value))

    def i32(self, value: int) -> None:
        self.stream.write(struct.pack("<i", value))

    def f32(self, value: float) -> None:
        self.stream.write(struct.pack("<f", value))

    def string(self, value: str, encoding: str = "ascii", static: bool = False) -> None:
        encoded = value.encode(encoding)
        count = len(encoded) // (2 if encoding == "utf-16-le" else 1)
        if not static:
            self.u32(count)
        self.bytes(encoded)


# ---------------------------------------------------------------------------
# Structured encrypted block codecs
# ---------------------------------------------------------------------------


def _signed(value: int) -> int:
    return value if value < 0x80000000 else value - 0x100000000


def _read_bool(stream: BinaryIO, cipher: Cipher) -> bool:
    value = cipher.read_bytes(stream, 1)[0]
    if value not in (0, 1):
        raise SaveFormatError(f"无效布尔值 {value}，文件位置 {stream.tell() - 1}")
    return bool(value)


def _write_bool(stream: BinaryIO, cipher: Cipher, value: bool) -> None:
    cipher.write_bytes(stream, bytes((1 if value else 0,)))


def _read_i32(stream: BinaryIO, cipher: Cipher) -> int:
    return _signed(cipher.read_u32(stream))


def _write_i32(stream: BinaryIO, cipher: Cipher, value: int) -> None:
    cipher.write_u32(stream, value)


def _read_f32(stream: BinaryIO, cipher: Cipher) -> float:
    bits = cipher.read_u32(stream)
    return struct.unpack("<f", _pack_u32(bits))[0]


def _write_f32(stream: BinaryIO, cipher: Cipher, value: float) -> None:
    cipher.write_u32(stream, struct.unpack("<I", struct.pack("<f", value))[0])


def _read_string(stream: BinaryIO, cipher: Cipher, encoding: str = "ascii") -> str:
    return _read_encrypted_string(stream, cipher, encoding)


def _write_string(
    stream: BinaryIO, cipher: Cipher, value: str, encoding: str = "ascii"
) -> None:
    _write_encrypted_string(stream, cipher, value, encoding)


def _read_array(stream: BinaryIO, cipher: Cipher, item_reader, count: int | None = None):
    actual_count = cipher.read_u32(stream) if count is None else count
    if actual_count > 10_000_000:
        raise SaveFormatError(f"数组数量异常：{actual_count}")
    return [item_reader(stream, cipher) for _ in range(actual_count)]


def _write_array(
    stream: BinaryIO, cipher: Cipher, values, item_writer, fixed: bool = False
) -> None:
    if not fixed:
        cipher.write_u32(stream, len(values))
    for value in values:
        item_writer(stream, cipher, value)


def _read_byte_value(stream: BinaryIO, cipher: Cipher) -> int:
    return cipher.read_bytes(stream, 1)[0]


def _write_byte_value(stream: BinaryIO, cipher: Cipher, value: int) -> None:
    cipher.write_bytes(stream, bytes((value & 0xFF,)))


def _read_int_value(stream: BinaryIO, cipher: Cipher) -> int:
    return _read_i32(stream, cipher)


def _write_int_value(stream: BinaryIO, cipher: Cipher, value: int) -> None:
    _write_i32(stream, cipher, value)


def _read_string_value(stream: BinaryIO, cipher: Cipher) -> str:
    return _read_string(stream, cipher)


def _write_string_value(stream: BinaryIO, cipher: Cipher, value: str) -> None:
    _write_string(stream, cipher, value)


def _read_uid(stream: BinaryIO, cipher: Cipher) -> bytes:
    return cipher.read_bytes(stream, 16)


def _write_uid(stream: BinaryIO, cipher: Cipher, value: bytes) -> None:
    if len(value) != 16:
        raise SaveFormatError("UID 必须为 16 字节")
    cipher.write_bytes(stream, value)


def _read_item(
    stream: BinaryIO,
    cipher: Cipher,
    positioned: bool,
    attached: bool,
    has_ascendant_fields: bool,
):
    item = {
        "basename": _read_string(stream, cipher),
        "prefix_name": _read_string(stream, cipher),
        "suffix_name": _read_string(stream, cipher),
        "modifier_name": _read_string(stream, cipher),
        "transmute_name": _read_string(stream, cipher),
        "seed": _read_i32(stream, cipher),
        "relic_name": _read_string(stream, cipher),
        "relic_bonus": _read_string(stream, cipher),
        "relic_seed": _read_i32(stream, cipher),
        "augment_name": _read_string(stream, cipher),
        "unknown": _read_i32(stream, cipher),
        "augment_seed": _read_i32(stream, cipher),
    }
    if has_ascendant_fields:
        item["ascendant_name"] = _read_string(stream, cipher)
        item["ascendant_seed"] = _read_i32(stream, cipher)
    item["relic_completion_level"] = _read_i32(stream, cipher)
    item["stack_count"] = _read_i32(stream, cipher)
    if has_ascendant_fields:
        item["v11_fields"] = [_read_i32(stream, cipher) for _ in range(2)]
    if positioned:
        item["x"] = _read_i32(stream, cipher)
        item["y"] = _read_i32(stream, cipher)
    if attached:
        item["attached"] = _read_bool(stream, cipher)
    return item


def _write_item(
    stream: BinaryIO,
    cipher: Cipher,
    item,
    positioned: bool,
    attached: bool,
    has_ascendant_fields: bool,
) -> None:
    for key in (
        "basename", "prefix_name", "suffix_name", "modifier_name", "transmute_name"
    ):
        _write_string(stream, cipher, item[key])
    _write_i32(stream, cipher, item["seed"])
    _write_string(stream, cipher, item["relic_name"])
    _write_string(stream, cipher, item["relic_bonus"])
    _write_i32(stream, cipher, item["relic_seed"])
    _write_string(stream, cipher, item["augment_name"])
    _write_i32(stream, cipher, item["unknown"])
    _write_i32(stream, cipher, item["augment_seed"])
    if has_ascendant_fields:
        _write_string(stream, cipher, item["ascendant_name"])
        _write_i32(stream, cipher, item["ascendant_seed"])
    _write_i32(stream, cipher, item["relic_completion_level"])
    _write_i32(stream, cipher, item["stack_count"])
    if has_ascendant_fields:
        for field_value in item["v11_fields"]:
            _write_i32(stream, cipher, field_value)
    if positioned:
        _write_i32(stream, cipher, item["x"])
        _write_i32(stream, cipher, item["y"])
    if attached:
        _write_bool(stream, cipher, item["attached"])


def _read_inventory_item(stream: BinaryIO, cipher: Cipher, has_ascendant_fields: bool):
    return _read_item(
        stream,
        cipher,
        positioned=True,
        attached=False,
        has_ascendant_fields=has_ascendant_fields,
    )


def _write_inventory_item(
    stream: BinaryIO, cipher: Cipher, item, has_ascendant_fields: bool
) -> None:
    _write_item(
        stream,
        cipher,
        item,
        positioned=True,
        attached=False,
        has_ascendant_fields=has_ascendant_fields,
    )


def _read_equipment_item(stream: BinaryIO, cipher: Cipher, has_ascendant_fields: bool):
    return _read_item(
        stream,
        cipher,
        positioned=False,
        attached=True,
        has_ascendant_fields=has_ascendant_fields,
    )


def _write_equipment_item(
    stream: BinaryIO, cipher: Cipher, item, has_ascendant_fields: bool
) -> None:
    _write_item(
        stream,
        cipher,
        item,
        positioned=False,
        attached=True,
        has_ascendant_fields=has_ascendant_fields,
    )


def _read_inventory_sack(
    stream: BinaryIO, cipher: Cipher, has_ascendant_fields: bool
):
    return {
        "unused": _read_bool(stream, cipher),
        "items": _read_array(
            stream,
            cipher,
            lambda s, c: _read_inventory_item(s, c, has_ascendant_fields),
        ),
    }


def _write_inventory_sack(
    stream: BinaryIO, cipher: Cipher, sack, has_ascendant_fields: bool
) -> None:
    _write_bool(stream, cipher, sack["unused"])
    _write_array(
        stream,
        cipher,
        sack["items"],
        lambda s, c, v: _write_inventory_item(
            s, c, v, has_ascendant_fields
        ),
    )


def _read_stash_item(stream: BinaryIO, cipher: Cipher, has_ascendant_fields: bool):
    return _read_item(
        stream,
        cipher,
        positioned=True,
        attached=False,
        has_ascendant_fields=has_ascendant_fields,
    )


def _write_stash_item(
    stream: BinaryIO, cipher: Cipher, item, has_ascendant_fields: bool
) -> None:
    _write_item(
        stream,
        cipher,
        item,
        positioned=True,
        attached=False,
        has_ascendant_fields=has_ascendant_fields,
    )


def _read_stash(stream: BinaryIO, cipher: Cipher, has_ascendant_fields: bool):
    stash = {
        "width": _read_i32(stream, cipher),
        "height": _read_i32(stream, cipher),
        "items": _read_array(
            stream,
            cipher,
            lambda s, c: _read_stash_item(s, c, has_ascendant_fields),
        ),
    }
    if has_ascendant_fields:
        stash["v5_fields"] = [_read_i32(stream, cipher) for _ in range(5)]
    return stash


def _write_stash(stream: BinaryIO, cipher: Cipher, stash, has_ascendant_fields: bool) -> None:
    _write_i32(stream, cipher, stash["width"])
    _write_i32(stream, cipher, stash["height"])
    _write_array(
        stream,
        cipher,
        stash["items"],
        lambda s, c, v: _write_stash_item(s, c, v, has_ascendant_fields),
    )
    if has_ascendant_fields:
        for field_value in stash["v5_fields"]:
            _write_i32(stream, cipher, field_value)


def _read_block3_tail(
    stream: BinaryIO, cipher: Cipher, has_ascendant_fields: bool
):
    return {
        "use_alt_weaponset": _read_bool(stream, cipher),
        "equipment": [
            _read_equipment_item(stream, cipher, has_ascendant_fields)
            for _ in range(12)
        ],
        "weapon_sets": [
            {
                "unused": _read_bool(stream, cipher),
                "items": [
                    _read_equipment_item(stream, cipher, has_ascendant_fields)
                    for _ in range(2)
                ],
            }
            for _ in range(2)
        ],
    }


def _write_block3_tail(
    stream: BinaryIO, cipher: Cipher, tail, has_ascendant_fields: bool
) -> None:
    _write_bool(stream, cipher, tail["use_alt_weaponset"])
    for item in tail["equipment"]:
        _write_equipment_item(stream, cipher, item, has_ascendant_fields)
    for weapon_set in tail["weapon_sets"]:
        _write_bool(stream, cipher, weapon_set["unused"])
        for item in weapon_set["items"]:
            _write_equipment_item(stream, cipher, item, has_ascendant_fields)


def _read_character_skill(
    stream: BinaryIO, cipher: Cipher, block_version: int
):
    skill = {
        "skill_name": _read_string(stream, cipher),
        "level": _read_i32(stream, cipher),
        "enabled": _read_bool(stream, cipher),
    }
    if block_version >= 8:
        skill["v8_field"] = _read_byte_value(stream, cipher)
    skill.update(
        {
            "devotion_level": _read_i32(stream, cipher),
            "devotion_experience": _read_i32(stream, cipher),
            "sublevel": _read_i32(stream, cipher),
            "active": _read_bool(stream, cipher),
            "transition": _read_bool(stream, cipher),
            "autocast_skill_name": _read_string(stream, cipher),
            "autocast_controller_name": _read_string(stream, cipher),
        }
    )
    return skill


def _write_character_skill(
    stream: BinaryIO, cipher: Cipher, skill, block_version: int
) -> None:
    _write_string(stream, cipher, skill["skill_name"])
    _write_i32(stream, cipher, skill["level"])
    _write_bool(stream, cipher, skill["enabled"])
    if block_version >= 8:
        _write_byte_value(stream, cipher, skill["v8_field"])
    _write_i32(stream, cipher, skill["devotion_level"])
    _write_i32(stream, cipher, skill["devotion_experience"])
    _write_i32(stream, cipher, skill["sublevel"])
    _write_bool(stream, cipher, skill["active"])
    _write_bool(stream, cipher, skill["transition"])
    _write_string(stream, cipher, skill["autocast_skill_name"])
    _write_string(stream, cipher, skill["autocast_controller_name"])


def _read_item_skill(stream: BinaryIO, cipher: Cipher):
    return {
        "skill_name": _read_string(stream, cipher),
        "autocast_skill_name": _read_string(stream, cipher),
        "autocast_controller_name": _read_string(stream, cipher),
        "unknown_bytes": cipher.read_bytes(stream, 4),
        "unknown": _read_string(stream, cipher),
    }


def _write_item_skill(stream: BinaryIO, cipher: Cipher, skill) -> None:
    _write_string(stream, cipher, skill["skill_name"])
    _write_string(stream, cipher, skill["autocast_skill_name"])
    _write_string(stream, cipher, skill["autocast_controller_name"])
    cipher.write_bytes(stream, skill["unknown_bytes"])
    _write_string(stream, cipher, skill["unknown"])


def _read_hotslot(stream: BinaryIO, cipher: Cipher):
    slot_type = _read_i32(stream, cipher)
    slot = {"type": slot_type}
    if slot_type == 0:
        slot.update(
            skill_name=_read_string(stream, cipher),
            is_item_skill=_read_bool(stream, cipher),
            item_name=_read_string(stream, cipher),
            item_equip_location=_read_i32(stream, cipher),
        )
    elif slot_type == 4:
        slot.update(
            item_name=_read_string(stream, cipher),
            bitmap_up=_read_string(stream, cipher),
            bitmap_down=_read_string(stream, cipher),
            default_text=_read_string(stream, cipher, "utf-16-le"),
        )
    return slot


def _write_hotslot(stream: BinaryIO, cipher: Cipher, slot) -> None:
    _write_i32(stream, cipher, slot["type"])
    if slot["type"] == 0:
        _write_string(stream, cipher, slot["skill_name"])
        _write_bool(stream, cipher, slot["is_item_skill"])
        _write_string(stream, cipher, slot["item_name"])
        _write_i32(stream, cipher, slot["item_equip_location"])
    elif slot["type"] == 4:
        _write_string(stream, cipher, slot["item_name"])
        _write_string(stream, cipher, slot["bitmap_up"])
        _write_string(stream, cipher, slot["bitmap_down"])
        _write_string(stream, cipher, slot["default_text"], "utf-16-le")


def _read_block_payload(
    block_id: int, stream: BinaryIO, cipher: Cipher, payload_end: int
):
    readers = {
        1: _read_block1,
        2: _read_block2,
        3: lambda s, c: _read_block3(s, c, payload_end),
        4: lambda s, c: _read_block4(s, c, payload_end),
        5: _read_block5,
        6: _read_block6,
        7: _read_block7,
        8: _read_block8,
        10: _read_block10,
        12: _read_block12,
        13: _read_block13,
        14: _read_block14,
        15: _read_block15,
        16: _read_block16,
        17: _read_block17,
    }
    reader = readers.get(block_id)
    if reader is None:
        raise SaveFormatError(f"暂不支持数据块 {block_id}")
    return reader(stream, cipher)


def _write_block_payload(
    stream: BinaryIO, cipher: Cipher, block_id: int, payload
) -> None:
    writers = {
        1: _write_block1,
        2: _write_block2,
        5: _write_block5,
        6: _write_block6,
        7: _write_block7,
        8: _write_block8,
        10: _write_block10,
        12: _write_block12,
        13: _write_block13,
        14: _write_block14,
        15: _write_block15,
        16: _write_block16,
        17: _write_block17,
    }
    writer = writers.get(block_id)
    if writer is None:
        raise SaveFormatError(f"暂不支持写入数据块 {block_id}")
    writer(stream, cipher, payload)


def _read_block1(stream: BinaryIO, cipher: Cipher):
    version = _read_i32(stream, cipher)
    result = {
        "version": version,
        "in_main_quest": _read_bool(stream, cipher),
        "has_been_in_game": _read_bool(stream, cipher),
        "last_difficulty": _read_byte_value(stream, cipher),
        "greatest_difficulty_completed": _read_byte_value(stream, cipher),
        "iron": _read_i32(stream, cipher),
        "greatest_survival_difficulty_completed": _read_byte_value(stream, cipher),
        "tributes": _read_i32(stream, cipher),
        "ui_compass_state": _read_byte_value(stream, cipher),
    }
    if 2 <= version <= 4:
        result["always_show_loot"] = _read_i32(stream, cipher)
    result.update(
        show_skill_help=_read_bool(stream, cipher),
        alt_weapon_set=_read_bool(stream, cipher),
        alt_weapon_set_enabled=_read_bool(stream, cipher),
        player_texture=_read_string(stream, cipher),
    )
    if version >= 5:
        result["loot_filters"] = _read_array(stream, cipher, _read_byte_value)
    return result


def _write_block1(stream: BinaryIO, cipher: Cipher, value) -> None:
    version = value["version"]
    _write_i32(stream, cipher, version)
    _write_bool(stream, cipher, value["in_main_quest"])
    _write_bool(stream, cipher, value["has_been_in_game"])
    _write_byte_value(stream, cipher, value["last_difficulty"])
    _write_byte_value(stream, cipher, value["greatest_difficulty_completed"])
    _write_i32(stream, cipher, value["iron"])
    _write_byte_value(stream, cipher, value["greatest_survival_difficulty_completed"])
    _write_i32(stream, cipher, value["tributes"])
    _write_byte_value(stream, cipher, value["ui_compass_state"])
    if 2 <= version <= 4:
        _write_i32(stream, cipher, value["always_show_loot"])
    _write_bool(stream, cipher, value["show_skill_help"])
    _write_bool(stream, cipher, value["alt_weapon_set"])
    _write_bool(stream, cipher, value["alt_weapon_set_enabled"])
    _write_string(stream, cipher, value["player_texture"])
    if version >= 5:
        _write_array(stream, cipher, value["loot_filters"], _write_byte_value)


def _read_block2(stream: BinaryIO, cipher: Cipher):
    return {
        "version": _read_i32(stream, cipher),
        "level": _read_i32(stream, cipher),
        "experience": _read_i32(stream, cipher),
        "attribute_points": _read_i32(stream, cipher),
        "skill_points": _read_i32(stream, cipher),
        "devotion_points": _read_i32(stream, cipher),
        "total_devotion_points_unlocked": _read_i32(stream, cipher),
        "physique": _read_f32(stream, cipher),
        "cunning": _read_f32(stream, cipher),
        "spirit": _read_f32(stream, cipher),
        "health": _read_f32(stream, cipher),
        "energy": _read_f32(stream, cipher),
    }


def _write_block2(stream: BinaryIO, cipher: Cipher, value) -> None:
    for key in (
        "version", "level", "experience", "attribute_points", "skill_points",
        "devotion_points", "total_devotion_points_unlocked",
    ):
        _write_i32(stream, cipher, value[key])
    for key in ("physique", "cunning", "spirit", "health", "energy"):
        _write_f32(stream, cipher, value[key])


def _read_uid_arrays(stream, cipher, outer_count):
    return [
        _read_array(stream, cipher, _read_uid) for _ in range(outer_count)
    ]


def _write_uid_arrays(stream, cipher, values):
    for inner in values:
        _write_array(stream, cipher, inner, _write_uid)


def _read_block5(stream, cipher):
    return {
        "version": _read_i32(stream, cipher),
        "spawn_points": _read_uid_arrays(stream, cipher, 3),
        "current_respawn": [_read_uid(stream, cipher) for _ in range(3)],
    }


def _write_block5(stream, cipher, value):
    _write_i32(stream, cipher, value["version"])
    _write_uid_arrays(stream, cipher, value["spawn_points"])
    _write_array(stream, cipher, value["current_respawn"], _write_uid, fixed=True)


def _read_block6(stream, cipher):
    return {"version": _read_i32(stream, cipher), "points": _read_uid_arrays(stream, cipher, 3)}


def _write_block6(stream, cipher, value):
    _write_i32(stream, cipher, value["version"]); _write_uid_arrays(stream, cipher, value["points"])


def _read_block7(stream, cipher):
    return {"version": _read_i32(stream, cipher), "markers": _read_uid_arrays(stream, cipher, 3)}


def _write_block7(stream, cipher, value):
    _write_i32(stream, cipher, value["version"]); _write_uid_arrays(stream, cipher, value["markers"])


def _read_block17(stream, cipher):
    return {"version": _read_i32(stream, cipher), "shrines": _read_uid_arrays(stream, cipher, 6)}


def _write_block17(stream, cipher, value):
    _write_i32(stream, cipher, value["version"]); _write_uid_arrays(stream, cipher, value["shrines"])


def _read_block8(stream, cipher):
    version = _read_i32(stream, cipher)
    value = {
        "version": version,
        "skills": _read_array(
            stream,
            cipher,
            lambda s, c: _read_character_skill(s, c, version),
        ),
        "masteries_allowed": _read_i32(stream, cipher),
        "skill_points_reclaimed": _read_i32(stream, cipher),
        "devotion_points_reclaimed": _read_i32(stream, cipher),
        "item_skills": _read_array(stream, cipher, _read_item_skill),
    }
    if version >= 6:
        value["unknown1"] = _read_i32(stream, cipher)
    return value


def _write_block8(stream, cipher, value):
    _write_i32(stream, cipher, value["version"])
    _write_array(
        stream,
        cipher,
        value["skills"],
        lambda s, c, v: _write_character_skill(
            s, c, v, value["version"]
        ),
    )
    _write_i32(stream, cipher, value["masteries_allowed"])
    _write_i32(stream, cipher, value["skill_points_reclaimed"])
    _write_i32(stream, cipher, value["devotion_points_reclaimed"])
    _write_array(stream, cipher, value["item_skills"], _write_item_skill)
    if value["version"] >= 6:
        _write_i32(stream, cipher, value["unknown1"])


def _read_block10(stream, cipher):
    return {
        "version": _read_i32(stream, cipher),
        "tokens": [
            _read_array(stream, cipher, _read_string_value) for _ in range(3)
        ],
    }


def _write_block10(stream, cipher, value):
    _write_i32(stream, cipher, value["version"])
    for tokens in value["tokens"]:
        _write_array(stream, cipher, tokens, _write_string_value)


def _read_block12(stream, cipher):
    return {"version": _read_i32(stream, cipher), "lore": _read_array(stream, cipher, _read_string_value)}


def _write_block12(stream, cipher, value):
    _write_i32(stream, cipher, value["version"]); _write_array(stream, cipher, value["lore"], _write_string_value)


def _read_faction(stream, cipher):
    return {
        "changed": _read_bool(stream, cipher), "unlocked": _read_bool(stream, cipher),
        "value": _read_f32(stream, cipher), "positive": _read_f32(stream, cipher),
        "negative": _read_f32(stream, cipher),
    }


def _write_faction(stream, cipher, value):
    _write_bool(stream, cipher, value["changed"]); _write_bool(stream, cipher, value["unlocked"])
    _write_f32(stream, cipher, value["value"]); _write_f32(stream, cipher, value["positive"]); _write_f32(stream, cipher, value["negative"])


def _read_block13(stream, cipher):
    return {"version": _read_i32(stream, cipher), "my_faction": _read_i32(stream, cipher), "factions": _read_array(stream, cipher, _read_faction)}


def _write_block13(stream, cipher, value):
    _write_i32(stream, cipher, value["version"]); _write_i32(stream, cipher, value["my_faction"]); _write_array(stream, cipher, value["factions"], _write_faction)


def _read_skill_set(stream, cipher):
    return {"primary": _read_string(stream, cipher), "secondary": _read_string(stream, cipher), "active": _read_bool(stream, cipher)}


def _write_skill_set(stream, cipher, value):
    _write_string(stream, cipher, value["primary"]); _write_string(stream, cipher, value["secondary"]); _write_bool(stream, cipher, value["active"])


def _read_block14(stream, cipher):
    version = _read_i32(stream, cipher)
    value = {
        "version": version, "equipment_selection": _read_bool(stream, cipher),
        "skill_window_selection": _read_i32(stream, cipher), "skill_setting_valid": _read_bool(stream, cipher),
        "skill_sets": [_read_skill_set(stream, cipher) for _ in range(5)],
    }
    if version >= 7:
        value["unknown2"] = _read_i32(stream, cipher); value["unknown3"] = _read_i32(stream, cipher); value["unknown4"] = _read_i32(stream, cipher)
    count = 36 if version == 4 else 46
    value["hotslots"] = [_read_hotslot(stream, cipher) for _ in range(count)]
    value["camera_distance"] = _read_f32(stream, cipher)
    if version >= 6:
        value["unknown1"] = _read_i32(stream, cipher)
    return value


def _write_block14(stream, cipher, value):
    version = value["version"]
    _write_i32(stream, cipher, version); _write_bool(stream, cipher, value["equipment_selection"]); _write_i32(stream, cipher, value["skill_window_selection"]); _write_bool(stream, cipher, value["skill_setting_valid"])
    for skill_set in value["skill_sets"]: _write_skill_set(stream, cipher, skill_set)
    if version >= 7:
        _write_i32(stream, cipher, value["unknown2"]); _write_i32(stream, cipher, value["unknown3"]); _write_i32(stream, cipher, value["unknown4"])
    for slot in value["hotslots"]: _write_hotslot(stream, cipher, slot)
    _write_f32(stream, cipher, value["camera_distance"])
    if version >= 6: _write_i32(stream, cipher, value["unknown1"])


def _read_block15(stream, cipher):
    return {"version": _read_i32(stream, cipher), "tutorials": _read_array(stream, cipher, _read_int_value)}


def _write_block15(stream, cipher, value):
    _write_i32(stream, cipher, value["version"]); _write_array(stream, cipher, value["tutorials"], _write_int_value)


def _read_monster(stream, cipher):
    return {"name": _read_string(stream, cipher), "level": _read_i32(stream, cipher), "life_mana": _read_i32(stream, cipher), "last_hit": _read_string(stream, cipher), "last_hit_by": _read_string(stream, cipher)}


def _write_monster(stream, cipher, value):
    _write_string(stream, cipher, value["name"]); _write_i32(stream, cipher, value["level"]); _write_i32(stream, cipher, value["life_mana"]); _write_string(stream, cipher, value["last_hit"]); _write_string(stream, cipher, value["last_hit_by"])


def _read_skill_map_entry(stream, cipher):
    return {"skill_name": _read_string(stream, cipher), "level": _read_i32(stream, cipher)}


def _write_skill_map_entry(stream, cipher, value):
    _write_string(stream, cipher, value["skill_name"]); _write_i32(stream, cipher, value["level"])


def _read_block16(stream, cipher):
    version = _read_i32(stream, cipher)
    value = {"version": version}
    int_keys = ("playtime_seconds", "death_count", "kill_count", "experience_from_kills", "health_potions_used", "energy_potions_used", "max_level", "hits_received", "hits_inflicted", "crits_inflicted", "crits_received")
    for key in int_keys: value[key] = _read_i32(stream, cipher)
    value["greatest_damage_done"] = _read_f32(stream, cipher)
    value["greatest_monsters"] = [_read_monster(stream, cipher) for _ in range(3)]
    value["champion_kills"] = _read_i32(stream, cipher); value["last_monster_hit_da"] = _read_f32(stream, cipher); value["last_monster_hit_oa"] = _read_f32(stream, cipher); value["greatest_damage_received"] = _read_f32(stream, cipher)
    for key in ("hero_kills", "items_crafted", "relics_crafted", "tier2_relics_crafted", "tier3_relics_crafted", "devotion_shrines_unlocked", "one_shot_chests_unlocked", "lore_notes_collected"):
        value[key] = _read_i32(stream, cipher)
    value["boss_kills"] = [_read_i32(stream, cipher) for _ in range(3)]
    for key in ("survival_greatest_wave", "survival_greatest_score", "survival_defense_built", "survival_powerups_activated"):
        value[key] = _read_i32(stream, cipher)
    if version >= 11:
        value["skills_map"] = _read_array(stream, cipher, _read_skill_map_entry)
        value["endless_souls"] = _read_i32(stream, cipher); value["endless_essence"] = _read_i32(stream, cipher); value["difficulty_skip"] = _read_byte_value(stream, cipher)
    value["unique_items_found"] = _read_i32(stream, cipher); value["randomized_items_found"] = _read_i32(stream, cipher)
    if version >= 12:
        value["v12_fields"] = [_read_i32(stream, cipher) for _ in range(2)]
    return value


def _write_block16(stream, cipher, value):
    version = value["version"]; _write_i32(stream, cipher, version)
    for key in ("playtime_seconds", "death_count", "kill_count", "experience_from_kills", "health_potions_used", "energy_potions_used", "max_level", "hits_received", "hits_inflicted", "crits_inflicted", "crits_received"):
        _write_i32(stream, cipher, value[key])
    _write_f32(stream, cipher, value["greatest_damage_done"])
    for monster in value["greatest_monsters"]: _write_monster(stream, cipher, monster)
    _write_i32(stream, cipher, value["champion_kills"]); _write_f32(stream, cipher, value["last_monster_hit_da"]); _write_f32(stream, cipher, value["last_monster_hit_oa"]); _write_f32(stream, cipher, value["greatest_damage_received"])
    for key in ("hero_kills", "items_crafted", "relics_crafted", "tier2_relics_crafted", "tier3_relics_crafted", "devotion_shrines_unlocked", "one_shot_chests_unlocked", "lore_notes_collected"):
        _write_i32(stream, cipher, value[key])
    for item in value["boss_kills"]: _write_i32(stream, cipher, item)
    for key in ("survival_greatest_wave", "survival_greatest_score", "survival_defense_built", "survival_powerups_activated"):
        _write_i32(stream, cipher, value[key])
    if version >= 11:
        _write_array(stream, cipher, value["skills_map"], _write_skill_map_entry); _write_i32(stream, cipher, value["endless_souls"]); _write_i32(stream, cipher, value["endless_essence"]); _write_byte_value(stream, cipher, value["difficulty_skip"])
    _write_i32(stream, cipher, value["unique_items_found"]); _write_i32(stream, cipher, value["randomized_items_found"])
    if version >= 12:
        for field_value in value["v12_fields"]:
            _write_i32(stream, cipher, field_value)
