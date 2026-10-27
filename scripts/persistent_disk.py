"""Helpers for a writable RedoxFS GPT disk; only called on newly created copies."""
import struct
import zlib


def grow_gpt(path, size):
    """Grow the final REDOX partition and relocate both GPT CRC-protected tables."""
    with path.open('r+b') as stream:
        mbr = stream.read(512)
        protective = [446+i*16 for i in range(4) if mbr[446+i*16+4] == 0xee]
        if mbr[510:512] != b'\x55\xaa' or len(protective) != 1:
            raise ValueError('Expected one protective MBR entry')
        stream.seek(512)
        header = bytearray(stream.read(512))
        if header[:8] != b'EFI PART' or size % 4096 or size <= path.stat().st_size:
            raise ValueError('Expected a GPT disk and a larger 4 KiB aligned size')
        header_size, checksum = struct.unpack_from('<II', header, 12)
        if not 92 <= header_size <= 512:
            raise ValueError('Invalid GPT header size')
        struct.pack_into('<I', header, 16, 0)
        if zlib.crc32(header[:header_size]) != checksum:
            raise ValueError('GPT header checksum mismatch')
        table_lba, count, entry_size, table_crc = struct.unpack_from('<QIII', header, 72)
        if entry_size != 128 or count > 4096:
            raise ValueError('Unsupported GPT entry geometry')
        stream.seek(table_lba*512)
        entries = bytearray(stream.read(count*entry_size))
        if zlib.crc32(entries) != table_crc:
            raise ValueError('GPT entries checksum mismatch')
        used = [i for i in range(count) if any(entries[i*entry_size:i*entry_size+16])]
        redox = [i for i in used if entries[i*entry_size+56:(i+1)*entry_size].decode('utf-16-le').rstrip('\0') == 'REDOX']
        if len(redox) != 1:
            raise ValueError('Expected one REDOX partition')
        index = redox[0]*entry_size
        start, old_end = struct.unpack_from('<QQ', entries, index+32)
        if any(struct.unpack_from('<Q', entries, i*entry_size+40)[0] > old_end for i in used):
            raise ValueError('REDOX must be the last partition')
        last = size//512-1
        table_sectors = (len(entries)+511)//512
        last_usable = last-table_sectors-1
        end = (last_usable+1)//8*8-1
        struct.pack_into('<Q', entries, index+40, end)
        struct.pack_into('<Q', header, 32, last)
        struct.pack_into('<Q', header, 48, last_usable)
        struct.pack_into('<I', header, 88, zlib.crc32(entries))
        backup = bytearray(header)
        struct.pack_into('<QQ', backup, 24, last, 1)
        struct.pack_into('<Q', backup, 72, last-table_sectors)
        for h in (header, backup):
            struct.pack_into('<I', h, 16, 0)
            struct.pack_into('<I', h, 16, zlib.crc32(h[:header_size]))
        stream.truncate(size)
        stream.seek(table_lba*512); stream.write(entries)
        stream.seek(512); stream.write(header)
        stream.seek((last-table_sectors)*512); stream.write(entries)
        stream.seek(last*512); stream.write(backup)
        # Protective MBR partition covers the enlarged GPT disk.
        stream.seek(protective[0]+12)
        stream.write(struct.pack('<I', min(last, 0xffffffff)))
        stream.flush()
        import os
        os.fsync(stream.fileno())
    return (end-start+1)*512
