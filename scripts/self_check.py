#!/usr/bin/env python3
"""Compatibility entry point for the complete offline Python regression suite.

Test cases live in test_*.py so ordinary unittest discovery collects the same
suite. Run either entry point once, not both.
"""
from collections import Counter
from pathlib import Path
import sys
import unittest

sys.dont_write_bytecode = True


def _test_ids(suite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from _test_ids(test)
        else:
            yield test.id()


def load_tests(loader, tests, pattern):
    suite = loader.discover(str(Path(__file__).parent), pattern='test_*.py')
    duplicates = sorted(test_id for test_id, count in Counter(_test_ids(suite)).items() if count > 1)
    if duplicates:
        raise RuntimeError('Duplicate regression test IDs: ' + ', '.join(duplicates))
    return suite


if __name__ == '__main__':
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    unittest.main(verbosity=2)
