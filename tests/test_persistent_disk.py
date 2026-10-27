import importlib.util
import pathlib
import struct
import sys
import tempfile
import unittest
import zlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]/'scripts'))
from persistent_disk import grow_gpt
from initfs_image import Initfs


class DiskTests(unittest.TestCase):
    def disk(self, path):
        size = 2*1024*1024
        entries = bytearray(128*128)
        entries[:16] = bytes(range(16))
        struct.pack_into('<QQ', entries, 32, 2048, 4000)
        entries[56:66] = 'REDOX'.encode('utf-16-le')
        header = bytearray(512)
        header[:8] = b'EFI PART'
        struct.pack_into('<IIIIQQQQ', header, 8, 0x10000, 92, 0, 0, 1, size//512-1, 34, size//512-34)
        struct.pack_into('<QIII', header, 72, 2, 128, 128, zlib.crc32(entries))
        struct.pack_into('<I', header, 16, zlib.crc32(header[:92]))
        with path.open('wb') as f:
            f.truncate(size)
            mbr = bytearray(512); mbr[450] = 0xee; mbr[510:512] = b'\x55\xaa'
            f.write(mbr); f.write(header); f.write(entries)
            f.seek(2048*512); f.write(b'FILESYSTEM-DATA-MUST-SURVIVE')

    def test_growth_preserves_data_and_both_gpt_checksums(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp)/'disk'
            self.disk(path)
            extent = grow_gpt(path, 8*1024*1024)
            data = path.read_bytes()
            self.assertEqual(data[2048*512:2048*512+len(b'FILESYSTEM-DATA-MUST-SURVIVE')], b'FILESYSTEM-DATA-MUST-SURVIVE')
            for lba in (1, len(data)//512-1):
                header = bytearray(data[lba*512:(lba+1)*512])
                checksum = struct.unpack_from('<I', header, 16)[0]
                struct.pack_into('<I', header, 16, 0)
                self.assertEqual(zlib.crc32(header[:92]), checksum)
                offset, count, size, crc = struct.unpack_from('<QIII', header, 72)
                table = data[offset*512:offset*512+count*size]
                self.assertEqual(zlib.crc32(table), crc)
                start, end = struct.unpack_from('<QQ', table, 32)
                self.assertEqual(extent, (end-start+1)*512)

    def test_bad_gpt_is_rejected_without_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp)/'disk'; self.disk(path)
            with path.open('r+b') as f: f.seek(512+50); f.write(b'bad')
            before = path.read_bytes()
            with self.assertRaises(ValueError): grow_gpt(path, 8*1024*1024)
            self.assertEqual(path.read_bytes(), before)

    def test_initfs_preserves_bootstrap_and_original_payloads(self):
        # One directory and one executable, with bootstrap bytes outside inodes.
        data = bytearray(5*4096)
        struct.pack_into('<8sIQHHHQ', data, 0, b'RedoxFtw', 4096, len(data), 4096, 0, 2, 0x1234)
        data[100:109] = b'BOOTSTRAP'
        struct.pack_into('<III', data, 4096, 2, 8, 8192)
        struct.pack_into('<III', data, 4108, 1, 4, 12288)
        struct.pack_into('<HHI', data, 8192, 1, 3, 16384)
        data[12288:12292] = b'CODE'; data[16384:16387] = b'old'
        original = Initfs(data)
        result = Initfs(original.add_executable('/new', b'NEW-DRIVER'))
        self.assertEqual(result.read('/old'), b'CODE')
        self.assertEqual(result.read('/new'), b'NEW-DRIVER')
        self.assertEqual(result.entry, 0x1234)
        self.assertEqual(result.data[100:109], b'BOOTSTRAP')
        with self.assertRaises(ValueError): result.add_executable('/new', b'replace')
        replaced = Initfs(result.replace_executable('/old', b'FIXED-CODE'))
        self.assertEqual(replaced.read('/old'), b'FIXED-CODE')
        self.assertEqual(replaced.read('/new'), b'NEW-DRIVER')
        self.assertEqual(replaced.entry, original.entry)
        self.assertEqual(replaced.data[100:109], b'BOOTSTRAP')
        with self.assertRaises(ValueError): replaced.replace_executable('/', b'bad')
