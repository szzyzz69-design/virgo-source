"""Create local credentials for a fresh source checkout without overwriting files."""
from pathlib import Path
import secrets

root = Path(__file__).resolve().parents[1]
targets = [root / '.env', root / 'config.toml', root / 'monitor/config.toml']
existing = [str(p.relative_to(root)) for p in targets if p.exists()]
if existing:
    raise SystemExit('Configuration already exists: ' + ', '.join(existing))

password = secrets.token_hex(24)
env = (
    'POSTGRES_PASSWORD=' + password + '\n'
    'SUPERVISOR_USERNAME=admin\n'
    'SUPERVISOR_PASSWORD=' + secrets.token_hex(24) + '\n'
    'SUPERVISOR_SESSION_SECRET=' + secrets.token_hex(32) + '\n'
    'SUPERVISOR_TIMEZONE=America/Vancouver\n'
    'VIRGO_HTTP_PORT=8000\nSUPERVISOR_HTTP_PORT=8010\n'
)
config = (root / 'config.example.toml').read_text(encoding='utf-8')
for placeholder in ['replace-with-a-long-random-registration-secret',
                    'replace-with-a-long-random-business-secret',
                    'replace-with-android-sms-gateway-webhook-secret']:
    config = config.replace(placeholder, secrets.token_hex(32))
(root / '.env').write_text(env, encoding='utf-8')
for path in targets[1:]:
    path.write_text(config, encoding='utf-8')
print('Created .env, config.toml and monitor/config.toml.')
print('The monitor login is stored in .env. Configure your own S3 storage for MMS.')
