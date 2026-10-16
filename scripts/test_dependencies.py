"""Dependency setup tests; installation and imports are mocked."""
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import ensure_dependencies as dependencies


class DependencyChecks(unittest.TestCase):
    def module(self):
        return SimpleNamespace(__version__='test', safe_load=lambda value: value)

    def test_installed_pyyaml_is_not_reinstalled(self):
        with patch.object(dependencies, 'load_yaml', return_value=self.module()), patch.object(dependencies.subprocess, 'run') as install:
            self.assertEqual(dependencies.ensure_pyyaml()['status'], 'already_installed')
            install.assert_not_called()

    def test_missing_pyyaml_installs_into_the_selected_python_then_verifies(self):
        with patch.object(dependencies, 'load_yaml', side_effect=[None, self.module()]) as load, patch.object(dependencies.subprocess, 'run') as install:
            self.assertEqual(dependencies.ensure_pyyaml()['status'], 'installed')
            self.assertEqual(load.call_count, 2)
            command = install.call_args.args[0]
            self.assertEqual(command[:4], [sys.executable, '-m', 'pip', 'install'])
            self.assertEqual(command[-1], 'PyYAML')
            self.assertNotIn('--upgrade', command)
            install.assert_called_once()

    def test_failed_install_does_not_retry(self):
        with patch.object(dependencies, 'load_yaml', return_value=None), patch.object(dependencies.subprocess, 'run', side_effect=dependencies.subprocess.CalledProcessError(1, ['pip'])) as install:
            with self.assertRaisesRegex(RuntimeError, '安装失败'):
                dependencies.ensure_pyyaml()
            install.assert_called_once()

    def test_broken_import_does_not_overwrite_an_existing_package(self):
        missing = ModuleNotFoundError('missing transitive dependency', name='other_dependency')
        with patch.object(dependencies.importlib, 'import_module', side_effect=missing), patch.object(dependencies.subprocess, 'run') as install:
            with self.assertRaisesRegex(RuntimeError, '依赖损坏'):
                dependencies.ensure_pyyaml()
            install.assert_not_called()

    def test_install_success_still_requires_a_working_import(self):
        with patch.object(dependencies, 'load_yaml', return_value=None), patch.object(dependencies.subprocess, 'run') as install:
            with self.assertRaisesRegex(RuntimeError, '仍无法导入'):
                dependencies.ensure_pyyaml()
            install.assert_called_once()


if __name__ == "__main__":
    unittest.main()
