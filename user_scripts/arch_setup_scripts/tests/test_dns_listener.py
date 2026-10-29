"""Check resolved socket ownership using captured ss-style fixtures."""
import os
from pathlib import Path
import subprocess
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/350_dns_systemd_resolve.sh'


class DnsListener(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = SCRIPT.read_text().rsplit('\nmain', 1)[0]
        start = source.index('# ------------------------------ Root escalation')
        end = source.index('# -------------------------------- Utilities')
        cls.source = source[:start] + source[end:]
        cls.source += '''
read_dns_listeners() {
    DNS_LISTENERS=$FIXTURE_LISTENERS
    RESOLVED_PID=1234
}
'''

    def socket(self, protocol, address='127.0.0.53:53', pid=1234):
        return f'{protocol} LISTEN 0 0 {address} 0.0.0.0:* users:(("systemd-resolve",pid={pid},fd=17))'

    def run_check(self, sockets, command='verify_stub_listener'):
        return subprocess.run(['bash', '-c', self.source + '\n' + command],
                              env=os.environ | {'FIXTURE_LISTENERS': '\n'.join(sockets)},
                              capture_output=True, text=True, timeout=5)

    def test_udp_and_tcp_accept_plain_or_interface_scoped_addresses(self):
        for address in ('127.0.0.53:53', '127.0.0.53%lo:53', '127.0.0.53%1:53'):
            with self.subTest(address=address):
                result = self.run_check([self.socket(protocol, address) for protocol in ('udp', 'tcp')])
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_wrong_owner_address_port_or_missing_protocol_is_rejected(self):
        valid = self.socket('udp')
        for tcp in (None, self.socket('tcp', pid=12345), self.socket('tcp', '127.0.0.54:53'),
                    self.socket('tcp', '127.0.0.53%lo:54')):
            with self.subTest(tcp=tcp):
                result = self.run_check([valid] + ([tcp] if tcp else []))
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)

    def test_conflict_detection_retains_pid_ownership_with_interface_suffix(self):
        own = self.socket('udp', '127.0.0.53%lo:53')
        result = self.run_check([own], '! detect_stub_conflict')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        other = self.socket('tcp', '127.0.0.53%lo:53', pid=9876)
        result = self.run_check([own, other], 'detect_stub_conflict')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
