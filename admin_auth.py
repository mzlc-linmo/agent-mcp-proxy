"""Single local administrator; no shared business credentials or default password."""
import hashlib
import hmac
import secrets
import time
from collections import OrderedDict

class AuthError(ValueError):
    def __init__(self,status=401):
        super().__init__('Authentication rejected');self.status=status

def password_hash(password):
    if not isinstance(password,str) or not 12<=len(password)<=256:
        raise ValueError('Password must contain 12 to 256 characters')
    salt=secrets.token_bytes(16)
    digest=hashlib.scrypt(password.encode(),salt=salt,n=16384,r=8,p=1,dklen=32)
    return 'scrypt$'+salt.hex()+'$'+digest.hex()

class Auth:
    def __init__(self,encoded,username):
        try:
            algorithm,salt,digest=encoded.split('$')
            if algorithm!='scrypt' or len(bytes.fromhex(salt))!=16 or len(bytes.fromhex(digest))!=32:raise ValueError()
        except Exception:raise ValueError('Valid administrator hash required') from None
        if not username or len(username)>64:raise ValueError('Administrator username required')
        self.salt=bytes.fromhex(salt);self.digest=bytes.fromhex(digest);self.username=username
        self.sessions=OrderedDict();self.failures=OrderedDict()

    def login(self,username,password,source):
        now=time.monotonic()
        attempts=[t for t in self.failures.get(source,[]) if now-t<600]
        if len(attempts)>=5:raise AuthError(429)
        if not isinstance(password,str) or len(password)>256:password=''
        actual=hashlib.scrypt(password.encode(),salt=self.salt,n=16384,r=8,p=1,dklen=32)
        if not hmac.compare_digest(actual,self.digest) or not hmac.compare_digest(str(username),self.username):
            self.failures[source]=attempts+[now];self.failures.move_to_end(source)
            while len(self.failures)>1024:self.failures.popitem(last=False)
            raise AuthError()
        self.failures.pop(source,None)
        for key in list(self.sessions):
            if self.sessions[key]['expires']<=now:self.sessions.pop(key,None)
        while len(self.sessions)>=64:self.sessions.popitem(last=False)
        token=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(32)
        self.sessions[token]={'username':self.username,'csrf':csrf,'expires':now+86400}
        return token,csrf

    def authenticate(self,token):
        session=self.sessions.get(token)
        if session is None or session['expires']<=time.monotonic():
            self.sessions.pop(token,None);raise AuthError()
        return session

    def logout(self,token):self.sessions.pop(token,None)
