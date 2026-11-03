"""Readiness must accept the exact new release and remain bounded on failure."""
from pathlib import Path
import subprocess
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from verify_live import wait_for_home


class Clock:
    def __init__(self):self.now=0;self.sleeps=[]
    def read(self):return self.now
    def sleep(self,seconds):self.sleeps.append(seconds);self.now+=seconds


class DeploymentTest(unittest.TestCase):
    def test_waits_past_old_configuration_and_wrong_homepage(self):
        replies=iter([(403,b'old root'),(200,b'old page'),(200,b'new page')])
        clock=Clock()
        wait_for_home(b'new page',probe=lambda:next(replies),clock=clock.read,sleep=clock.sleep)
        self.assertEqual(clock.sleeps,[.25,.25])

    def test_persistent_failure_times_out_with_actual_status(self):
        clock=Clock()
        with self.assertRaisesRegex(RuntimeError,'HTTP 403'):
            wait_for_home(b'new page',probe=lambda:(403,b'old root'),timeout=1,clock=clock.read,sleep=clock.sleep)
        self.assertEqual(clock.now,1)

    def test_wrong_content_never_counts_as_ready(self):
        clock=Clock()
        with self.assertRaisesRegex(RuntimeError,'different homepage content'):
            wait_for_home(b'new page',probe=lambda:(200,b'other page'),timeout=.5,clock=clock.read,sleep=clock.sleep)

    def test_certificate_or_transport_errors_are_not_ignored(self):
        def failed():raise subprocess.CalledProcessError(60,['curl'])
        with self.assertRaises(subprocess.CalledProcessError):wait_for_home(b'new page',probe=failed)


if __name__=='__main__':unittest.main()
