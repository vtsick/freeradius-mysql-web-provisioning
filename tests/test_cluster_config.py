import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cluster_config import load_cluster_config, node_url, selected_node
import container_start


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads(Path('cluster.json').read_text())

    def load(self, config):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json') as source:
            json.dump(config, source)
            source.flush()
            with patch.dict(os.environ, {'CLUSTER_CONFIG': source.name}):
                return load_cluster_config()

    def test_supplied_config_and_target_selection(self):
        config = self.load(self.config)
        for index, node in enumerate(config['nodes']):
            with patch.dict(os.environ, {'PROVISIONING_NODE': node['name'],
                                        'DB_USER': 'tester', 'DB_PASSWORD': 'a@:/#$'}):
                target = selected_node(config)
                url = node_url(target)
                self.assertEqual(url.host, f'fr-{index + 1}')
                self.assertEqual(url.password, 'a@:/#$')
                self.assertEqual(target['http_port'], 8000 + index)
                self.assertEqual(target['gunicorn_port'], 5000 + index)

    def test_invalid_config_rejected(self):
        for field, value in (('http_port', 5000), ('sql_port', 0),
                             ('gunicorn_port', '5001'), ('name', 'fr-1'), ('host', '')):
            with self.subTest(field=field):
                config = copy.deepcopy(self.config)
                config['nodes'][1][field] = value
                with self.assertRaises(ValueError):
                    self.load(config)

    def test_unknown_target_and_missing_credentials(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError):
                selected_node(self.config)
            with self.assertRaises(ValueError):
                node_url(self.config['nodes'][0])

    def test_expected_size_may_include_arbiter(self):
        self.config['expected_cluster_size'] = 5
        self.assertEqual(self.load(self.config)['expected_cluster_size'], 5)
        self.config['expected_cluster_size'] = 3
        with self.assertRaises(ValueError):
            self.load(self.config)

    def test_startup_renders_matching_ports_and_preserves_nginx_variables(self):
        template = Path('nginx/app.conf').read_text()
        for node in self.config['nodes']:
            with self.subTest(node=node['name']):
                with patch.object(container_start, 'load_cluster_config', return_value=self.config), \
                     patch.object(container_start, 'selected_node', return_value=node), \
                     patch.object(container_start.Path, 'read_text', return_value=template), \
                     patch.object(container_start.Path, 'write_text') as write, \
                     patch.object(container_start.subprocess, 'run') as run, \
                     patch.object(container_start.os, 'execvp') as execute, \
                     patch.object(container_start.sys, 'argv', ['container_start.py', 'app:app']):
                    container_start.main()
                    rendered = write.call_args.args[0]
                    self.assertIn(f"server 127.0.0.1:{node['gunicorn_port']};", rendered)
                    self.assertIn(f"listen {node['http_port']};", rendered)
                    self.assertIn('proxy_set_header Host $host;', rendered)
                    self.assertNotIn('${', rendered)
                    self.assertEqual(execute.call_args.args[1][-1],
                                     f"127.0.0.1:{node['gunicorn_port']}")
                    self.assertEqual(run.call_args_list[0].args[0], ['nginx', '-t'])
