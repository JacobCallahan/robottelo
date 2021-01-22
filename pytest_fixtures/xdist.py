"""Fixtures specific to or relating to pytest's xdist plugin"""
from uuid import uuid4
import uuid
import pytest
from broker.broker import VMBroker

from robottelo.config import settings
from robottelo.utils import setup_entities

XDIST_ID = str(uuid4())

@pytest.fixture(scope="session", autouse=True)
def align_to_satellite(worker_id, behavior='run-on-one', satellite_factory):
    if worker_id == 'master':
        worker_pos = 0
    else:
        worker_pos = int(worker_id.replace('gw', ''))
    if worker_pos < len(settings.hostnames):
        settings.hostname = settings.hostname[worker_pos]
    elif behavior == 'run-on-one' and settings.hostnames:
            settings.hostname = settings.hostnames[0]
    elif behavior == 'balance' and settings.hostnames:
        import random
        settings.hostname = random.choice(settings.hostnames)
    # get current satellite information
    elif behavior == "on-demand":
        sat = satellite_factory(xdist_id=XDIST_ID)
        if sat.hostname:
            settings.hostname = sat.hostname
    setup_entities.configure_nailgun(settings)
    setup_entities.configure_airgun(settings)


@pytest.fixture(scope="session", autouse=True)
def checkin_xdist_satellite():
    yield
    if settings.auto_checkin:
        host = VMBroker().from_inventory(filter=f'_broker_args.xdist_id={XDIST_ID}')
        VMBroker().checkin(host=host)
