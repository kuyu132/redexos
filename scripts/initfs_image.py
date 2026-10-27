"""Append files to the pinned RedoxFtw format without relocating bootstrap code.

Format: upstream/qemu-base/initfs/src/types.rs. Existing file offsets and inode
numbers stay fixed. Only the target directory and inode table are copied.
"""
import struct
from pathlib import PurePosixPath


class Initfs:
    def __init__(self, data):
        self.data = bytes(data)
        magic, self.table, size, self.page, self.root, count, self.entry = struct.unpack_from('<8sIQHHHQ', data)
        if magic != b'RedoxFtw' or size != len(data) or self.page != 4096:
            raise ValueError('Unsupported initfs header')
        self.inodes = [struct.unpack_from('<III', self.slice(self.table+i*12, 12)) for i in range(count)]
        for kind, length, offset in self.inodes:
            if kind not in range(4):
                raise ValueError('Unsupported inode type')
            self.slice(offset, length)

    def slice(self, offset, size):
        if offset < 0 or offset+size > len(self.data):
            raise ValueError('Invalid initfs extent')
        return self.data[offset:offset+size]

    def entries(self, inode):
        kind, length, offset = self.inodes[inode]
        if kind != 2 or length % 8:
            raise ValueError('Expected directory')
        result = {}
        for pos in range(offset, offset+length, 8):
            child, name_length, name_offset = struct.unpack_from('<HHI', self.data, pos)
            name = self.slice(name_offset, name_length).decode()
            if child >= len(self.inodes) or name in result or name in ('.', '..') or '/' in name:
                raise ValueError('Invalid directory entry')
            result[name] = child
        return result

    def lookup(self, path):
        path = PurePosixPath(path)
        if '..' in path.parts:
            raise ValueError('Relative traversal is unsupported')
        node = self.root
        for part in path.parts:
            if part != '/':
                node = self.entries(node)[part]
        return node

    def read(self, path):
        kind, length, offset = self.inodes[self.lookup(path)]
        if kind not in (0, 1):
            raise ValueError('Expected regular file')
        return self.slice(offset, length)

    def add_executable(self, path, payload):
        path = PurePosixPath(path)
        parent = self.lookup(str(path.parent))
        entries = self.entries(parent)
        if path.name in entries:
            raise ValueError(f'Refusing existing initfs entry: {path}')
        if len(self.inodes) == 65535:
            raise ValueError('Too many inodes')
        data = bytearray(self.data)
        def append(blob):
            data.extend(b'\0' * (-len(data) % self.page))
            offset = len(data)
            data.extend(blob)
            return offset
        offset = append(payload)
        inodes = list(self.inodes) + [(1, len(payload), offset)]
        name = path.name.encode()
        name_offset = append(name)
        _, length, old_offset = inodes[parent]
        directory = self.slice(old_offset, length) + struct.pack('<HHI', len(self.inodes), len(name), name_offset)
        inodes[parent] = (2, len(directory), append(directory))
        table = append(b''.join(struct.pack('<III', *inode) for inode in inodes))
        data.extend(b'\0' * (-len(data) % self.page))
        struct.pack_into('<I', data, 8, table)
        struct.pack_into('<Q', data, 12, len(data))
        struct.pack_into('<H', data, 24, len(inodes))
        result = Initfs(data)
        assert result.read(str(path)) == payload
        assert result.entry == self.entry
        # Every original non-directory payload remains byte-for-byte intact.
        for index, (kind, length, offset) in enumerate(self.inodes):
            if kind != 2:
                assert result.inodes[index] == self.inodes[index]
                assert result.slice(offset, length) == self.slice(offset, length)
        return bytes(data)

    def replace_executable(self, path, payload):
        """Replace one executable while retaining bootstrap and other payloads."""
        node = self.lookup(path)
        if self.inodes[node][0] != 1:
            raise ValueError('Expected an existing executable')
        data = bytearray(self.data)
        data.extend(b'\0' * (-len(data) % self.page))
        offset = len(data)
        data.extend(payload)
        inodes = list(self.inodes)
        inodes[node] = (1, len(payload), offset)
        data.extend(b'\0' * (-len(data) % self.page))
        table = len(data)
        data.extend(b''.join(struct.pack('<III', *entry) for entry in inodes))
        data.extend(b'\0' * (-len(data) % self.page))
        struct.pack_into('<I', data, 8, table)
        struct.pack_into('<Q', data, 12, len(data))
        result = Initfs(data)
        assert result.read(path) == payload and result.entry == self.entry
        for index, entry in enumerate(self.inodes):
            if index != node:
                assert result.inodes[index] == entry
                _, length, start = entry
                assert result.slice(start, length) == self.slice(start, length)
        return bytes(data)
