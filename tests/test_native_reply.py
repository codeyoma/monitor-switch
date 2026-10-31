import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


class ReplyTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('cc'), 'Native helper validation needs a C compiler.')
    def test_real_dell_reply_and_invalid_frames(self):
        root = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'check.c'
            binary = Path(directory) / 'check'
            source.write_text('''
#include "ddc_reply.h"
#include <assert.h>
int main(void) {
    uint8_t data[] = {0x6e,0x88,0x02,0x00,0x60,0x00,0x00,0x0e,0x11,0x0f,0xc4,0x75};
    int current=0, maximum=0;
    assert(monitor_switch_reply(data,12,0x60,&current,&maximum));
    assert(current == 0x110f && maximum == 14);
    assert(!monitor_switch_reply(data,10,0x60,&current,&maximum));
    assert(!monitor_switch_reply(data,12,0xf4,&current,&maximum));
    data[10] ^= 1;
    assert(!monitor_switch_reply(data,12,0x60,&current,&maximum));
    data[10] ^= 1;
    data[3] = 1; data[10] ^= 1;
    assert(!monitor_switch_reply(data,12,0x60,&current,&maximum));
    uint8_t empty[12] = {0};
    assert(!monitor_switch_reply(empty,12,0x60,&current,&maximum));
    uint8_t write[256] = {0x84,0x03,0x60,0x00,0xd8,0x00};
    assert(monitor_switch_packet_length(write,sizeof(write)) == 6);
    uint8_t read[256] = {0x82,0x01,0x60,0x00};
    assert(monitor_switch_packet_length(read,sizeof(read)) == 4);
    assert(monitor_switch_packet_length(write,5) == 0);
    return 0;
}
''')
            subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-I', str(root / 'native'),
                            str(source), '-o', str(binary)], check=True)
            subprocess.run([str(binary)], check=True)
