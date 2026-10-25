import importlib.util
import base64
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

spec = importlib.util.spec_from_file_location('share_folder', Path(__file__).resolve().parents[1] / 'share_folder.py')
share = importlib.util.module_from_spec(spec)
spec.loader.exec_module(share)


class ShareConfigurationTests(unittest.TestCase):
    def domain(self):
        return ET.fromstring('<domain><name>fixture</name><devices><disk type="file">'
                             '<source file="/preserved.qcow2"/></disk><shmem name="looking-glass"/>'
                             '</devices></domain>')

    def test_add_preserves_disk_and_looking_glass(self):
        root = self.domain()
        share.configure(root, Path('/mnt/zram1'))
        self.assertEqual(root.find('./devices/disk/source').get('file'), '/preserved.qcow2')
        self.assertIsNotNone(root.find('./devices/shmem'))
        self.assertEqual(root.find('./memoryBacking/source').get('type'), 'memfd')
        self.assertEqual(root.find('./memoryBacking/access').get('mode'), 'shared')

    def test_converged_and_path_change(self):
        root = share.configure(self.domain(), Path('/mnt/zram1'))
        before = ET.tostring(root)
        share.configure(root, Path('/mnt/zram1'))
        self.assertEqual(ET.tostring(root), before)
        share.configure(root, Path('/custom folder'))
        self.assertEqual(len(root.findall('./devices/filesystem')), 1)
        self.assertEqual(root.find('./devices/filesystem/source').get('dir'), '/custom folder')
        self.assertEqual(root.find('./devices/filesystem/binary/cache').get('mode'), 'none')

    def test_hugepages_and_file_backing_preserved(self):
        root = self.domain()
        backing = ET.SubElement(root, 'memoryBacking')
        ET.SubElement(backing, 'hugepages')
        ET.SubElement(backing, 'source', type='file')
        share.configure(root, Path('/share'))
        self.assertIsNotNone(root.find('./memoryBacking/hugepages'))
        self.assertEqual(root.find('./memoryBacking/source').get('type'), 'file')

    def test_foreign_share_not_repurposed(self):
        root = self.domain()
        fs = ET.SubElement(root.find('devices'), 'filesystem')
        ET.SubElement(fs, 'driver', type='virtiofs')
        ET.SubElement(fs, 'target', dir='someone-else')
        before = ET.tostring(root)
        with self.assertRaisesRegex(RuntimeError, 'Another VirtIO-FS'):
            share.configure(root, Path('/share'))
        self.assertEqual(ET.tostring(root), before)

    def test_private_memory_backing_rejected(self):
        root = self.domain()
        backing = ET.SubElement(root, 'memoryBacking')
        ET.SubElement(backing, 'source', type='anonymous')
        with self.assertRaisesRegex(RuntimeError, 'memory backing'):
            share.configure(root, Path('/share'))

    def test_existing_tag_with_another_driver_rejected(self):
        root = self.domain()
        fs = ET.SubElement(root.find('devices'), 'filesystem')
        ET.SubElement(fs, 'target', dir=share.TAG)
        with self.assertRaisesRegex(RuntimeError, 'different filesystem driver'):
            share.configure(root, Path('/share'))

    def test_guest_errors_readable_without_progress_noise(self):
        xml = '#< CLIXML\r\n<Objs><Obj S="progress"/><S S="Error">Missing driver_x000D__x000A_</S></Objs>'
        self.assertEqual(share.guest_text(base64.b64encode(xml.encode())), 'Missing driver')


if __name__ == '__main__':
    unittest.main()
