"""Interactive credential initialization. Never prints a password or accepts one in argv."""
import getpass
import os
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from admin_auth import password_hash

if __name__=='__main__':
    path=Path(os.environ.get('ADMIN_HASH_FILE',str(ROOT/'secrets'/'admin_hash')))
    if path.exists():raise SystemExit('Administrator hash already exists; explicit rotation required')
    password=getpass.getpass('New administrator password (12+ characters): ')
    if password!=getpass.getpass('Confirm password: '):raise SystemExit('Passwords differ')
    encoded=password_hash(password)
    path.parent.mkdir(parents=True,exist_ok=True)
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as stream:stream.write(encoded+'\n')
    print('Administrator hash initialized; no password was saved or displayed')
