"""Read QEMU Ethernet PCAP evidence for DHCP negotiation and paired DNS lookups."""
import socket
import struct


def dns_question(data):
    labels, position, end = [], 12, None
    for _ in range(128):
        length = data[position]
        if length & 0xc0 == 0xc0:
            if end is None:
                end = position+2
            position = ((length & 0x3f) << 8) | data[position+1]
        elif length == 0:
            if end is None:
                end = position+1
            return '.'.join(labels).lower(), struct.unpack_from('!H', data, end)[0]
        elif length <= 63:
            position += 1
            labels.append(data[position:position+length].decode('ascii'))
            position += length
        else:
            raise ValueError('Invalid DNS label')
    raise ValueError('DNS compression loop')


def inspect_capture(path):
    raw = path.read_bytes()
    if raw[:4] == b'\xd4\xc3\xb2\xa1':
        endian = '<'
    elif raw[:4] == b'\xa1\xb2\xc3\xd4':
        endian = '>'
    else:
        raise ValueError('Unsupported PCAP format')
    if struct.unpack_from(endian+'I', raw, 20)[0] != 1:
        raise ValueError('Expected Ethernet PCAP')
    position, pending = 24, set()
    result = {'frames':0, 'dhcp':[], 'dns':[]}
    while position < len(raw):
        if position+16 > len(raw):
            raise ValueError('Truncated packet header')
        _, _, size, _ = struct.unpack_from(endian+'IIII', raw, position)
        position += 16
        frame = raw[position:position+size]
        position += size
        if len(frame) != size:
            raise ValueError('Truncated packet')
        result['frames'] += 1
        if len(frame) < 34 or frame[12:14] != b'\x08\x00':
            continue
        ip = frame[14:]
        header_size = (ip[0] & 15)*4
        if header_size < 20 or ip[9] != 17 or len(ip) < header_size+8:
            continue
        source, destination = socket.inet_ntoa(ip[12:16]), socket.inet_ntoa(ip[16:20])
        source_port, destination_port, length, _ = struct.unpack_from('!HHHH', ip, header_size)
        data = ip[header_size+8:header_size+length]
        if {source_port, destination_port} == {67, 68} and len(data) >= 240 and data[236:240] == b'\x63\x82\x53\x63':
            options, index = {}, 240
            while index < len(data):
                tag = data[index]; index += 1
                if tag == 255:
                    break
                if tag == 0:
                    continue
                if index >= len(data):
                    raise ValueError('Truncated DHCP option')
                count = data[index]; index += 1
                value = data[index:index+count]; index += count
                if len(value) != count:
                    raise ValueError('Truncated DHCP option value')
                options[tag] = value
            if 53 in options:
                entry = {'type':options[53][0], 'xid':data[4:8].hex(),
                         'client_mac':data[28:34].hex(':'), 'offered_address':socket.inet_ntoa(data[16:20])}
                for tag, name in ((1,'mask'), (3,'gateway'), (6,'dns'), (54,'server')):
                    if tag in options and len(options[tag]) >= 4:
                        entry[name] = socket.inet_ntoa(options[tag][:4])
                result['dhcp'].append(entry)
        elif 53 in (source_port, destination_port) and len(data) >= 12:
            try:
                name, kind = dns_question(data)
            except (ValueError, IndexError, UnicodeDecodeError, struct.error):
                continue
            transaction, flags, _, answers = struct.unpack_from('!HHHH', data)
            response = bool(flags & 0x8000)
            key = (destination if response else source,
                   destination_port if response else source_port, transaction, name, kind)
            if not response:
                pending.add(key)
            result['dns'].append({'name':name, 'type':kind, 'response':response,
                                  'rcode':flags & 15, 'answers':answers,
                                  'paired':response and key in pending})
    return result


def require_network_evidence(capture, hostname):
    # Match one transaction, not an unrelated collection of packet types.
    for discover in capture['dhcp']:
        if discover['type'] != 1:
            continue
        exchange = [p for p in capture['dhcp'] if p['xid'] == discover['xid'] and p['client_mac'] == discover['client_mac']]
        types = [p['type'] for p in exchange]
        acknowledgements = [p for p in exchange if p['type'] == 5 and p['offered_address'] == '10.0.2.15'
                            and p.get('gateway') == '10.0.2.2' and p.get('dns') == '10.0.2.3']
        if all(t in types for t in (1,2,3,5)) and acknowledgements:
            break
    else:
        raise AssertionError('Missing DHCP Discover/Offer/Request/Ack transaction')
    if not any(p['name'] == hostname.lower() and p['type'] == 1 and p['response']
               and p['paired'] and p['rcode'] == 0 and p['answers'] > 0 for p in capture['dns']):
        raise AssertionError('Missing successful paired DNS A query and response')
