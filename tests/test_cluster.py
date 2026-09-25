import unittest
from unittest.mock import MagicMock, patch

from sqlalchemy.exc import OperationalError

from app import app
import app as application
from copy import deepcopy


class ClusterRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        self.variables = {'wsrep_on': 'ON', 'wsrep_provider': '/lib/libgalera_smm.so'}
        self.status = {
            'wsrep_cluster_status': 'Primary',
            'wsrep_cluster_size': '3',
            'wsrep_connected': 'ON',
            'wsrep_ready': 'ON',
            'wsrep_local_state': '4',
            'wsrep_local_state_comment': 'Synced',
        }

    def request_status(self):
        with patch('app.engine') as engine, patch('app.logger'):
            connection = engine.connect.return_value.__enter__.return_value
            connection.execute.side_effect = [
                MagicMock(fetchall=lambda: list(self.variables.items())),
                MagicMock(fetchall=lambda: list(self.status.items())),
            ]
            response = self.client.get('/chkcluster')
            engine.connect.return_value.__exit__.assert_called_once()
            return response

    def test_healthy_node(self):
        response = self.request_status()
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body['success'])
        self.assertTrue(body['healthy'])
        self.assertEqual(body['cluster_size'], 3)
        self.assertEqual(body['status'], self.status)

    def test_unhealthy_or_missing_status(self):
        healthy_status = self.status.copy()
        for key, value in (
            ('wsrep_cluster_status', 'non-Primary'),
            ('wsrep_connected', 'OFF'),
            ('wsrep_ready', 'OFF'),
            ('wsrep_local_state', '2'),
            ('wsrep_cluster_size', '0'),
            ('wsrep_cluster_size', 'invalid'),
            ('wsrep_ready', None),
        ):
            with self.subTest(key=key, value=value):
                self.status = dict(healthy_status, **{key: value})
                response = self.request_status()
                self.assertEqual(response.status_code, 503)
                body = response.get_json()
                self.assertFalse(body['success'])
                self.assertFalse(body['details']['healthy'])
                self.assertTrue(body['details']['reasons'])

    def test_disabled_or_unavailable_galera(self):
        for variables in ({}, {'wsrep_on': 'OFF'},
                          {'wsrep_on': 'ON', 'wsrep_provider': 'none'}):
            with self.subTest(variables=variables):
                self.variables = variables
                response = self.request_status()
                self.assertEqual(response.status_code, 503)
                self.assertFalse(response.get_json()['details']['galera_enabled'])

    def test_database_failure_is_sanitized(self):
        for during_query in (False, True):
            with self.subTest(during_query=during_query):
                with patch('app.engine') as engine, patch('app.logger'):
                    error = OperationalError('secret connection info', {}, Exception('secret'))
                    if during_query:
                        connection = engine.connect.return_value.__enter__.return_value
                        connection.execute.side_effect = error
                    else:
                        engine.connect.side_effect = error
                    response = self.client.get('/chkcluster')
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.get_json()['error_type'], 'Database Error')
                self.assertNotIn('secret', response.get_data(as_text=True))


class AggregateClusterTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        self.config = {'expected_cluster_size': None}
        self.results = {
            name: {
                'healthy': True, 'galera_enabled': True, 'cluster_size': 5,
                'scope': 'connected_node', 'reasons': [],
                'status': {'wsrep_cluster_state_uuid': 'cluster-a',
                           'wsrep_local_state_uuid': 'cluster-a'},
            }
            for name in ('fr-1', 'fr-2', 'fr-3', 'fr-4')
        }

    def request_status(self):
        def read(name):
            result = self.results[name]
            if isinstance(result, Exception):
                raise result
            return deepcopy(result)

        with patch.object(application, 'cluster_config', self.config), \
             patch.object(application, 'provisioning_node', {'name': 'fr-2'}), \
             patch.object(application, 'cluster_engines', {name: name for name in self.results}), \
             patch.object(application, 'read_cluster_node', side_effect=read) as check, \
             patch.object(application, 'logger'):
            response = self.client.get('/chkcluster')
            self.assertEqual(check.call_count, 4)
            return response

    def test_all_members_checked_with_selected_provisioning_target(self):
        response = self.request_status()
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body['provisioning_node'], 'fr-2')
        self.assertEqual([m['name'] for m in body['members']], list(self.results))

    def test_unavailable_member_preserves_other_results(self):
        self.results['fr-3'] = application.ApiError('secret connection information', 503)
        response = self.request_status()
        self.assertEqual(response.status_code, 503)
        members = response.get_json()['details']['members']
        self.assertEqual([m['healthy'] for m in members], [True, True, False, True])
        self.assertNotIn('secret', response.get_data(as_text=True))

    def test_different_cluster_uuid_rejected_even_when_locally_synced(self):
        self.results['fr-4']['status'] = {
            'wsrep_cluster_state_uuid': 'cluster-b', 'wsrep_local_state_uuid': 'cluster-b',
        }
        self.assertEqual(self.request_status().status_code, 503)

    def test_missing_or_mismatched_local_uuid(self):
        for value in (None, 'old-state'):
            with self.subTest(value=value):
                self.results['fr-2']['status']['wsrep_local_state_uuid'] = value
                self.assertEqual(self.request_status().status_code, 503)

    def test_size_disagreement_and_expected_size(self):
        self.results['fr-4']['cluster_size'] = 4
        self.assertEqual(self.request_status().status_code, 503)
        self.results['fr-4']['cluster_size'] = 5
        self.config['expected_cluster_size'] = 5
        self.assertEqual(self.request_status().status_code, 200)
        self.config['expected_cluster_size'] = 6
        self.assertEqual(self.request_status().status_code, 503)


if __name__ == '__main__':
    unittest.main()
