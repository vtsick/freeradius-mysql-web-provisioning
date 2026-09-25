import os
from pathlib import Path
import subprocess
import sys

from cluster_config import load_cluster_config, selected_node


def main():
    config = load_cluster_config()
    node = selected_node(config) if config else {'gunicorn_port': 5000, 'http_port': 8000}
    template = Path('/etc/nginx/templates/app.conf.template').read_text()
    rendered = template.replace('${GUNICORN_PORT}', str(node['gunicorn_port']))
    rendered = rendered.replace('${HTTP_PORT}', str(node['http_port']))
    Path('/etc/nginx/conf.d/app.conf').write_text(rendered)
    subprocess.run(['nginx', '-t'], check=True)
    subprocess.run(['nginx'], check=True)
    os.execvp('gunicorn', ['gunicorn', *sys.argv[1:],
                          '--bind', f"127.0.0.1:{node['gunicorn_port']}"])


if __name__ == '__main__':
    main()
