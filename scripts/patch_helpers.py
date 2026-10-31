"""Apply small, auditable changes to the pinned upstream command-line tools."""
import shutil
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def patch(name, directory):
    if name == 'm1ddc':
        source = directory / 'sources/m1ddc.m'
        text = source.read_text()
        marker = 'MonitorSwitch reply validation: v1'
        if marker not in text:
            anchor = 'static void printDisplayInfos(DisplayInfos *display, int nbDisplays, bool detailed) {'
            assert anchor in text
            text = text.replace(anchor, anchor + '\n    writeToStdOut(@"' + marker + '\\n");')
            source.write_text(text)
        if 'monitor_switch_reply' not in text:
            old = '    return convertI2CtoDDC((char *)readPacket.data);'
            assert old in text, 'Unexpected m1ddc source; refusing an unsafe patch.'
            text = '#include "ddc_reply.h"\n' + text
            text = text.replace(old, '''    int current = 0, maximum = 0;
    if (!monitor_switch_reply(readPacket.data, 12, packet->data[2], &current, &maximum)) {
        writeToStdOut(@"Invalid or unsupported DDC reply (header, status, VCP or checksum).\\n");
        return dummyAttr;
    }
    DDCValue value = {current, maximum};
    return value;''')
            source.write_text(text)
        shutil.copy2(ROOT / 'native/ddc_reply.h', directory / 'headers/ddc_reply.h')
        # Upstream uses sizeof(2), i.e. four bytes, for two-byte uint16_t destinations.
        i2c = directory / 'sources/i2c.m'
        text = i2c.read_text().replace('length:sizeof(2)', 'length:sizeof(uint16_t)')
        assert 'length:sizeof(2)' not in text
        if 'monitor_switch_packet_length' not in text:
            text = re.sub(r'static int getBytesUsed\(UInt8\* data\) \{.*?\n\}\n', '', text, count=1, flags=re.S)
            text = '#include "ddc_reply.h"\n' + text
            assert 'getBytesUsed(packet->data)' in text
            text = text.replace('getBytesUsed(packet->data)',
                                'monitor_switch_packet_length(packet->data, sizeof(packet->data))')
        i2c.write_text(text)
    elif name == 'ddcctl':
        source = directory / 'src/ddcctl.m'
        text = source.read_text()
        if 'MonitorSwitch UUID support' in text:
            if 'MonitorSwitch display' not in text:
                anchor = '                NSSize displayPixelSize = '
                position = text.find(anchor)
                assert position >= 0
                text = text[:position] + '                MyLog(@"I: MonitorSwitch display %@ name: %@", screenUUIDstr, screen.localizedName);\n' + text[position:]
                source.write_text(text)
            return
        old = '            else if (!strcmp(argv[i], "-b")) {'
        assert old in text, 'Unexpected ddcctl source; refusing an unsafe patch.'
        text = text.replace(old, '''            else if (!strcmp(argv[i], "-u")) {
                i++;
                if (i >= argc) return 1;
                NSString *wanted = [NSString stringWithUTF8String:argv[i]];
                displayId = 0;
                for (NSUInteger j = 0; j < [_displayIDs count]; j++) {
                    CFUUIDRef uuid = CGDisplayCreateUUIDFromDisplayID([_displayIDs[j] unsignedIntValue]);
                    CFStringRef value = CFUUIDCreateString(NULL, uuid);
                    BOOL matches = [wanted caseInsensitiveCompare:(__bridge NSString *)value] == NSOrderedSame;
                    CFRelease(value);
                    CFRelease(uuid);
                    if (matches) { displayId = j + 1; break; }
                }
                if (displayId == 0) { MyLog(@"E: Requested UUID is not connected"); return 1; }
            }

''' + old)
        text = text.replace('    @autoreleasepool {', '    @autoreleasepool {\n        MyLog(@"I: MonitorSwitch UUID support: v1");', 1)
        anchor = '                NSSize displayPixelSize = '
        position = text.find(anchor)
        assert position >= 0
        text = text[:position] + '                MyLog(@"I: MonitorSwitch display %@ name: %@", screenUUIDstr, screen.localizedName);\n' + text[position:]
        source.write_text(text)
    else:
        raise ValueError('Unknown helper.')


if __name__ == '__main__':
    patch(sys.argv[1], Path(sys.argv[2]))
