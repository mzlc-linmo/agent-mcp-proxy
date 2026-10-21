"""ASGI factory; missing administrator credentials fail closed before serving HTTP."""
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from admin import create_app
from admin_auth import Auth
from admin_config import ConfigStore
from endpoint_manager import EndpointManager


def create_runtime():
    origin=os.environ['ADMIN_ORIGIN']
    auth=Auth(Path(os.environ.get('ADMIN_HASH_FILE','/run/secrets/admin_hash')).read_text().strip(),os.environ.get('ADMIN_USERNAME','admin'))
    path=Path(os.environ.get('ADMIN_CONFIG','/var/lib/linmo-proxy/config.json'))
    if not path.exists():
        ConfigStore(path,os.environ.get('ADMIN_ALLOWED_UPSTREAM_ORIGIN','http://higress:8080')).import_legacy(Path(os.environ.get('BRIDGE_CONFIG','/app/config.json')))
    from catalog import CatalogStore
    store=CatalogStore(path)
    manager=EndpointManager()
    app=create_app(store,manager,auth,origin)

    @asynccontextmanager
    async def lifespan(app):
        await manager.start(store.load())
        try:yield
        finally:await manager.stop()
    app.router.lifespan_context=lifespan
    from proxy import secure_logging
    secure_logging()
    return app
