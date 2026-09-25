import json
import os

from sqlalchemy.engine import URL


def load_cluster_config():
    path = os.environ.get('CLUSTER_CONFIG')
    if not path:
        return None
    with open(path, encoding='utf-8') as source:
        config = json.load(source)
    if not isinstance(config, dict):
        raise ValueError('Cluster configuration must be a JSON object')
    nodes = config.get('nodes')
    if not isinstance(nodes, list) or not 2 <= len(nodes) <= 4:
        raise ValueError('Cluster configuration requires 2 to 4 database nodes')
    names, ports = set(), set()
    for node in nodes:
        if not isinstance(node, dict):
            raise ValueError('Each node must be a JSON object')
        name = node.get('name')
        if not isinstance(name, str) or not name or name in names:
            raise ValueError('Node names must be nonempty and unique')
        names.add(name)
        for field in ('host', 'database'):
            if not isinstance(node.get(field), str) or not node[field].strip():
                raise ValueError(f'{name}: {field} must be a nonempty string')
        for field in ('sql_port', 'gunicorn_port', 'http_port'):
            port = node.get(field)
            if type(port) is not int or not 1 <= port <= 65535:
                raise ValueError(f'{name}: invalid {field}')
            if field != 'sql_port':
                if port in ports:
                    raise ValueError('HTTP and Gunicorn ports must be unique')
                ports.add(port)
    expected = config.get('expected_cluster_size')
    if expected is not None and (type(expected) is not int or expected < len(nodes)):
        raise ValueError('Expected cluster size must include every configured node')
    return config


def selected_node(config):
    name = os.environ.get('PROVISIONING_NODE')
    for node in config['nodes']:
        if node['name'] == name:
            return node
    raise ValueError('PROVISIONING_NODE must name a configured node')


def node_url(node):
    user_key = node.get('user_env', 'DB_USER')
    password_key = node.get('password_env', 'DB_PASSWORD')
    if not os.environ.get(user_key) or not os.environ.get(password_key):
        raise ValueError(f"Missing database credentials for {node['name']}")
    return URL.create(
        'mysql+pymysql', username=os.environ[user_key], password=os.environ[password_key],
        host=node['host'], port=node['sql_port'], database=node['database'],
    )
