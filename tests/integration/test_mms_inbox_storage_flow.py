"""Opt-in check using a disposable PG instance and a real private MinIO bucket."""
import base64
from dataclasses import replace
from datetime import datetime, timezone
import os
from pathlib import Path
from urllib.request import urlopen
from uuid import uuid4

import boto3
import httpx
from botocore.config import Config
from fastapi.testclient import TestClient
import psycopg
import pytest

from app.application import create_app
from app.config import Settings
from app.database import Database
from app.security import hash_password
from app.services.mms_webhook_service import MmsWebhookService
from app.services.object_storage import ObjectStorageUnavailable


@pytest.mark.skipif(not os.getenv('MMS_S3_TEST_CONFIG'), reason='requires isolated MinIO configuration')
def test_device_mms_roundtrip_private_download_and_storage_recovery(clean_database, monkeypatch):
    config = Path(os.environ['MMS_S3_TEST_CONFIG']).resolve()
    monkeypatch.setenv('DATABASE_URL', clean_database.dsn)
    monkeypatch.setenv('VIRGO_CONFIG_FILE', str(config))
    settings = replace(Settings.from_env(), s3_endpoint_url='http://127.0.0.1:9000')
    account_id = 'acct_' + uuid4().hex
    other_id = 'acct_' + uuid4().hex
    username = 'mms-agent-' + uuid4().hex
    other_username = 'mms-agent-' + uuid4().hex
    sender = clean_database.track_phone('+1' + str(uuid4().int)[:10])
    marker = clean_database.track_push_token('mms-storage-' + uuid4().hex)
    password = 'mms-isolated-test-only'
    data = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jBn8AAAAASUVORK5CYII=')
    keys = []
    def online_client():
        if os.getenv('MMS_API_TEST_URL'):
            return httpx.Client(base_url=os.environ['MMS_API_TEST_URL'], timeout=15)
        return TestClient(create_app(settings))
    try:
        with online_client() as client:
            registered = client.post('/mobile/v1/device', json={
                'name': marker, 'pushToken': marker,
                'simCards': [{'simNumber': 1, 'slotIndex': 0, 'phoneNumber': '+14155550199'}],
            }, headers={'Authorization': 'Bearer ' + settings.private_registration_token})
            assert registered.status_code == 201, registered.text
            device_id = registered.json()['id']
            token = registered.json()['token']
            clean_database.track(device_id)
            with psycopg.connect(clean_database.dsn) as connection:
                sim_id = connection.execute('SELECT id FROM sim_cards WHERE device_id=%s', (device_id,)).fetchone()[0]
                for aid, name in ((account_id, username), (other_id, other_username)):
                    connection.execute("INSERT INTO accounts(id,username,password_hash,areas) VALUES(%s,%s,%s,'mms-test')", (aid, name, hash_password(password)))
                connection.execute('INSERT INTO account_sim_cards(account_id,sim_card_id) VALUES(%s,%s)', (account_id, sim_id))
            body = {
                'messageId': 'mms-test-' + uuid4().hex, 'sender': sender,
                'recipient': '+14155550199', 'simNumber': 1, 'body': '彩信联调',
                'receivedAt': datetime.now(timezone.utc).isoformat(),
                # Deliberately omit size: server must persist the actual byte length.
                'attachments': [{'partId': 17, 'name': 'test.png', 'contentType': 'image/png',
                                 'data': base64.b64encode(data).decode()}],
            }
            headers = {'Authorization': f'Bearer {token}'}
            first = client.post('/mobile/v1/inbox/mms', json=body, headers=headers)
            assert first.status_code == 201, first.text
            with psycopg.connect(clean_database.dsn) as connection:
                keys.extend(row[0] for row in connection.execute('SELECT s3_key FROM message_attachments WHERE message_id=%s', (first.json()['id'],)).fetchall())
            conversation_id = first.json()['conversationId']
            repeated = client.post('/mobile/v1/inbox/mms', json=body, headers=headers)
            assert repeated.status_code == 200 and repeated.json()['created'] is False
            assert client.post('/mobile/v1/inbox/mms', json={**body, 'body': 'changed'}, headers=headers).status_code == 409
            assert client.post('/mobile/v1/inbox/mms', json=body).status_code == 401
            logins = []
            for name in (username, other_username):
                login = client.post('/agent/v1/auth/login', json={'username': name, 'password': password})
                assert login.status_code == 200, login.text
                logins.append({'Authorization': 'Bearer ' + login.json()['token']})
            response = client.get(f'/agent/v1/conversations/{conversation_id}/messages', headers=logins[0])
            assert response.status_code == 200, response.text
            attachment = response.json()[0]['attachments'][0]
            assert attachment['size'] == len(data)
            with urlopen(attachment['url'], timeout=10) as downloaded:
                assert downloaded.read() == data
            assert client.get(f'/agent/v1/conversations/{conversation_id}/messages', headers=logins[1]).status_code == 403
            with psycopg.connect(clean_database.dsn) as connection:
                assert connection.execute('SELECT unread_count FROM conversations WHERE id=%s', (conversation_id,)).fetchone()[0] == 1

        class OfflineStorage:
            def upload_bytes(self, **kwargs):
                raise ObjectStorageUnavailable

        retry_body = {**body, 'messageId': 'retry-' + uuid4().hex}
        offline = MmsWebhookService(Database(clean_database.dsn), OfflineStorage())
        with TestClient(create_app(settings, mms_webhook_service=offline)) as client:
            assert client.post('/mobile/v1/inbox/mms', json=retry_body, headers=headers).status_code == 503
        with psycopg.connect(clean_database.dsn) as connection:
            assert connection.execute('SELECT count(*) FROM messages WHERE device_id=%s', (device_id,)).fetchone()[0] == 1
            assert connection.execute('SELECT unread_count FROM conversations WHERE id=%s', (conversation_id,)).fetchone()[0] == 1
        with online_client() as client:
            retried = client.post('/mobile/v1/inbox/mms', json=retry_body, headers=headers)
            assert retried.status_code == 201, retried.text
        with psycopg.connect(clean_database.dsn) as connection:
            keys.extend(row[0] for row in connection.execute('SELECT s3_key FROM message_attachments WHERE message_id=%s', (retried.json()['id'],)).fetchall())
    finally:
        with psycopg.connect(clean_database.dsn) as connection:
            connection.execute('DELETE FROM accounts WHERE id=ANY(%s::varchar[])', ([account_id, other_id],))
        # Admin credentials are used only to remove this test's explicitly tracked objects.
        if keys:
            credentials = dict(line.split('=', 1) for line in (config.parent / '.env.mms').read_text().splitlines() if line and not line.startswith('#'))
            admin = boto3.client('s3', endpoint_url='http://127.0.0.1:9000', region_name='us-east-1',
                                 aws_access_key_id=credentials['MINIO_ROOT_USER'],
                                 aws_secret_access_key=credentials['MINIO_ROOT_PASSWORD'],
                                 config=Config(signature_version='s3v4', s3={'addressing_style': 'path'}))
            for key in keys:
                admin.delete_object(Bucket=settings.s3_bucket, Key=key)
