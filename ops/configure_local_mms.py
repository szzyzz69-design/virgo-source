"""Create private local MinIO credentials and update only S3 config values."""
import argparse
import json
from pathlib import Path
import re
import secrets
import tomllib
from urllib.parse import urlsplit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default='config.toml')
    parser.add_argument('--credentials', default='.env.mms')
    parser.add_argument('--upload-endpoint', default='http://host.docker.internal:9000')
    parser.add_argument('--download-endpoint', required=True,
                        help='Endpoint reachable by the agent phones, including :9000')
    args = parser.parse_args()
    for endpoint in (args.upload_endpoint, args.download_endpoint):
        parsed = urlsplit(endpoint)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
            parser.error('S3 endpoints must be http(s) URLs without credentials')

    credentials_path = Path(args.credentials)
    if credentials_path.exists():
        credentials = dict(line.split('=', 1) for line in credentials_path.read_text().splitlines()
                           if line and not line.startswith('#'))
    else:
        credentials = {
            'MINIO_ROOT_USER': 'virgo-admin-' + secrets.token_hex(8),
            'MINIO_ROOT_PASSWORD': secrets.token_urlsafe(36),
            'MINIO_APP_USER': 'virgo-mms-' + secrets.token_hex(8),
            'MINIO_APP_PASSWORD': secrets.token_urlsafe(36),
        }
        credentials_path.write_text(''.join(f'{key}={value}\n' for key, value in credentials.items()), encoding='utf-8')
    required = ('MINIO_ROOT_USER', 'MINIO_ROOT_PASSWORD', 'MINIO_APP_USER', 'MINIO_APP_PASSWORD')
    if any(not credentials.get(key) for key in required):
        parser.error('Existing credentials file is incomplete; preserve it and repair the missing fields')

    config_path = Path(args.config)
    if not config_path.exists():
        parser.error('Create config.toml and configure the registration/business tokens first')
    contents = config_path.read_text(encoding='utf-8')
    tomllib.loads(contents)
    values = {
        's3_endpoint_url': args.upload_endpoint.rstrip('/'),
        's3_download_endpoint_url': args.download_endpoint.rstrip('/'),
        's3_region': 'us-east-1',
        's3_bucket': 'virgo-mms',
        's3_access_key_id': credentials['MINIO_APP_USER'],
        's3_secret_access_key': credentials['MINIO_APP_PASSWORD'],
        's3_public_base_url': '',
        's3_addressing_style': 'path',
    }
    for key, value in values.items():
        line = f'{key} = {json.dumps(value)}'
        pattern = rf'(?m)^{re.escape(key)}\s*=.*$'
        if re.search(pattern, contents):
            contents = re.sub(pattern, lambda match: line, contents)
        else:
            contents = contents.rstrip() + '\n' + line + '\n'
    tomllib.loads(contents)
    config_path.write_text(contents, encoding='utf-8')
    print(f'Updated S3 config in {config_path}; credentials retained privately in {credentials_path}')


if __name__ == '__main__':
    main()
