import unittest

from ramdisk_fs_server.ramdisk import _extract_device


class ExtractDeviceTests(unittest.TestCase):
    def test_extracts_first_disk_device(self) -> None:
        stdout = "/dev/disk4\n/dev/disk4s1"
        self.assertEqual(_extract_device(stdout), "/dev/disk4")

    def test_raises_when_device_missing(self) -> None:
        with self.assertRaises(ValueError):
            _extract_device("no device here")


if __name__ == "__main__":
    unittest.main()
