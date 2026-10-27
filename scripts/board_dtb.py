#!/usr/bin/env python3
"""Small, dependency-free FDT reader for Raspberry Pi early-boot validation."""
from dataclasses import dataclass
from pathlib import Path
import struct


FDT_MAGIC = 0xD00DFEED
FDT_BEGIN_NODE, FDT_END_NODE, FDT_PROP, FDT_NOP, FDT_END = 1, 2, 3, 4, 9


def align4(value):
    return (value + 3) & ~3


def cells(data):
    if len(data) % 4:
        raise ValueError("FDT cell property is not 32-bit aligned")
    return list(struct.unpack(f">{len(data) // 4}I", data))


def cell_value(values):
    result = 0
    for value in values:
        result = (result << 32) | value
    return result


@dataclass(frozen=True)
class Node:
    path: str
    properties: dict


class Fdt:
    def __init__(self, source):
        data = Path(source).read_bytes() if isinstance(source, (str, Path)) else bytes(source)
        if len(data) < 40:
            raise ValueError("FDT header is truncated")
        header = struct.unpack_from(">10I", data)
        magic, total, struct_off, strings_off = header[:4]
        strings_size, struct_size = header[8], header[9]
        if magic != FDT_MAGIC or total > len(data):
            raise ValueError("Invalid FDT header")
        structure = data[struct_off:struct_off + struct_size]
        strings = data[strings_off:strings_off + strings_size]
        self.nodes = self._parse(structure, strings)

    @staticmethod
    def _parse(structure, strings):
        nodes, stack, offset = {}, [], 0
        while offset + 4 <= len(structure):
            token = struct.unpack_from(">I", structure, offset)[0]
            offset += 4
            if token == FDT_BEGIN_NODE:
                end = structure.find(b"\0", offset)
                if end < 0:
                    raise ValueError("Unterminated FDT node name")
                name = structure[offset:end].decode()
                offset = align4(end + 1)
                path = "/" if not stack else stack[-1].rstrip("/") + "/" + name
                stack.append(path)
                nodes[path] = Node(path, {})
            elif token == FDT_END_NODE:
                if not stack:
                    raise ValueError("Unbalanced FDT node")
                stack.pop()
            elif token == FDT_PROP:
                if not stack or offset + 8 > len(structure):
                    raise ValueError("Invalid FDT property")
                length, name_offset = struct.unpack_from(">II", structure, offset)
                offset += 8
                value = structure[offset:offset + length]
                offset = align4(offset + length)
                name_end = strings.find(b"\0", name_offset)
                if name_end < 0:
                    raise ValueError("Invalid FDT property name")
                name = strings[name_offset:name_end].decode()
                nodes[stack[-1]].properties[name] = value
            elif token == FDT_NOP:
                continue
            elif token == FDT_END:
                if stack:
                    raise ValueError("Unbalanced FDT structure")
                return nodes
            else:
                raise ValueError(f"Unknown FDT token {token}")
        raise ValueError("FDT_END missing")

    def node(self, path):
        try:
            return self.nodes[path]
        except KeyError as error:
            raise ValueError(f"FDT node missing: {path}") from error

    def prop(self, path, name):
        try:
            return self.node(path).properties[name]
        except KeyError as error:
            raise ValueError(f"FDT property missing: {path}:{name}") from error

    def strings(self, path, name):
        raw = self.prop(path, name)
        return [part.decode() for part in raw.rstrip(b"\0").split(b"\0")]

    def text(self, path, name):
        return self.strings(path, name)[0]

    def integers(self, path, name):
        return cells(self.prop(path, name))

    @staticmethod
    def parent(path):
        if path == "/":
            return None
        parent = path.rsplit("/", 1)[0]
        return parent or "/"

    def integer(self, path, name, default=None):
        try:
            values = self.integers(path, name)
        except ValueError:
            if default is not None:
                return default
            raise
        if len(values) != 1:
            raise ValueError(f"Expected one cell: {path}:{name}")
        return values[0]

    def resolve_stdout(self):
        stdout = self.text("/chosen", "stdout-path")
        name, _, params = stdout.partition(":")
        path = name if name.startswith("/") else self.text("/aliases", name)
        return path, params

    def translated_reg(self, path, index=0):
        parent = self.parent(path)
        if parent is None:
            raise ValueError("Root node has no reg address space")
        address_cells = self.integer(parent, "#address-cells", 2)
        size_cells = self.integer(parent, "#size-cells", 1)
        width = address_cells + size_cells
        values = self.integers(path, "reg")
        start = index * width
        if start + width > len(values):
            raise ValueError(f"reg index {index} is missing at {path}")
        address = cell_value(values[start:start + address_cells])
        size = cell_value(values[start + address_cells:start + width])
        bus = parent
        while bus != "/":
            upstream = self.parent(bus)
            child_cells = self.integer(bus, "#address-cells", 2)
            parent_cells = self.integer(upstream, "#address-cells", 2)
            range_size_cells = self.integer(bus, "#size-cells", 1)
            raw = self.prop(bus, "ranges")
            if raw:
                entries = cells(raw)
                stride = child_cells + parent_cells + range_size_cells
                if stride == 0 or len(entries) % stride:
                    raise ValueError(f"Malformed ranges at {bus}")
                translated = None
                for offset in range(0, len(entries), stride):
                    child = cell_value(entries[offset:offset + child_cells])
                    parent_address = cell_value(entries[offset + child_cells:offset + child_cells + parent_cells])
                    extent = cell_value(entries[offset + child_cells + parent_cells:offset + stride])
                    if address >= child and size <= extent and address - child <= extent - size:
                        translated = parent_address + address - child
                        break
                if translated is None:
                    raise ValueError(f"No ranges entry maps {path} through {bus}")
                address = translated
            bus = upstream
        return address, size

    def find_phandle(self, value):
        for node in self.nodes.values():
            for name in ("phandle", "linux,phandle"):
                raw = node.properties.get(name)
                if raw is not None and cells(raw) == [value]:
                    return node.path
        raise ValueError(f"FDT phandle {value} is unresolved")


BOARD_CONTRACTS = {
    "pi4b": {
        "compatible": "raspberrypi,4-model-b",
        "model": "Raspberry Pi 4 Model B",
        "stdout_alias": "serial0",
        "uart_compatible": "arm,pl011",
        "uart_address": 0xFE201000,
        "uart_size": 0x200,
        "uart_interrupt": [0, 121, 4],
        "gic_address": 0xFF841000,
    },
    "pi5": {
        "compatible": "raspberrypi,5-model-b",
        "model": "Raspberry Pi 5",
        "stdout_alias": "serial10",
        "uart_compatible": "arm,pl011",
        "uart_address": 0x107D001000,
        "uart_size": 0x200,
        "uart_interrupt": [0, 121, 4],
        "gic_address": 0x107FFF9000,
    },
}


def validate(board, source):
    expected = BOARD_CONTRACTS[board]
    fdt = Fdt(source)
    if expected["compatible"] not in fdt.strings("/", "compatible"):
        raise ValueError(f"{board}: incompatible DTB")
    if fdt.text("/", "model") != expected["model"]:
        raise ValueError(f"{board}: unexpected model")
    raw_stdout = fdt.text("/chosen", "stdout-path")
    if raw_stdout.split(":", 1)[0] != expected["stdout_alias"]:
        raise ValueError(f"{board}: stdout alias is {raw_stdout!r}")
    uart, params = fdt.resolve_stdout()
    if expected["uart_compatible"] not in fdt.strings(uart, "compatible"):
        raise ValueError(f"{board}: selected UART is not PL011")
    if fdt.node(uart).properties.get("status", b"okay\0").rstrip(b"\0") not in (b"ok", b"okay"):
        raise ValueError(f"{board}: selected UART is disabled")
    uart_address, uart_size = fdt.translated_reg(uart)
    if (uart_address, uart_size) != (expected["uart_address"], expected["uart_size"]):
        raise ValueError(f"{board}: wrong UART range {uart_address:#x}/{uart_size:#x}")
    interrupts = fdt.integers(uart, "interrupts")
    if interrupts[:3] != expected["uart_interrupt"]:
        raise ValueError(f"{board}: unexpected UART interrupt {interrupts}")
    root_parent = fdt.integer("/", "interrupt-parent")
    gic = fdt.find_phandle(root_parent)
    if "arm,gic-400" not in fdt.strings(gic, "compatible"):
        raise ValueError(f"{board}: root interrupt parent is not GIC-400")
    gic_address, _ = fdt.translated_reg(gic)
    if gic_address != expected["gic_address"]:
        raise ValueError(f"{board}: wrong GIC address {gic_address:#x}")
    timer = next((node.path for node in fdt.nodes.values()
                  if "arm,armv8-timer" in [part.decode() for part in node.properties.get("compatible", b"").rstrip(b"\0").split(b"\0") if part]), None)
    if timer is None or len(fdt.integers(timer, "interrupts")) < 12:
        raise ValueError(f"{board}: architected timer contract missing")
    return {
        "board": board,
        "model": expected["model"],
        "stdout_path": raw_stdout,
        "stdout_params": params,
        "uart_node": uart,
        "uart_physical_address": f"0x{uart_address:x}",
        "uart_size": f"0x{uart_size:x}",
        "uart_interrupt": interrupts[:3],
        "gic_node": gic,
        "gic_physical_address": f"0x{gic_address:x}",
        "timer_node": timer,
    }
