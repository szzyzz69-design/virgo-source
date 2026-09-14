"""End-to-end diagnostics tests. Run against an isolated TEST_DATABASE_URL."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.application import create_app
from app.config import Settings
from app.database import Database
from app.services.sms_check_service import SmsCheckService
from app.services.message_pull_service import MessagePullService

BUSINESS = {'Authorization':'Bearer business-secret'}
SOURCE = '+16045550101'
SECOND = '+16045550102'
TARGET = '+16045550103'


@pytest.fixture
def gateway(clean_database):
    # These tests launch all-SIM diagnostics: refuse to run on the production DB.
    assert ':55439/' in clean_database.dsn, 'SMS-check tests require the isolated database on port 55439'
    class Publisher:
        def __init__(self): self.events=[]
        def publish(self,*args): self.events.append(args)
    outbound,inbound=Publisher(),Publisher()
    app=create_app(Settings(clean_database.dsn,'registration-secret','business-secret'),
        message_publisher=outbound,inbound_publisher=inbound)
    with TestClient(app) as client:
        def register(phones):
            response=client.post('/mobile/v1/device',headers={'Authorization':'Bearer registration-secret'},json={
                'name':'diagnostic-test','pushToken':'pytest-check-'+uuid4().hex,
                'simCards':[{'slotIndex':i,'simNumber':i+1,'phoneNumber':phone} for i,phone in enumerate(phones)]})
            assert response.status_code==201,response.text
            value=response.json()
            clean_database.track(value['id'])
            return value
        sender=register([SOURCE,SECOND])
        receiver=register([TARGET])
        service=SmsCheckService(Database(clean_database.dsn))
        yield client,sender,receiver,service,clean_database,outbound,inbound
    with psycopg.connect(clean_database.dsn) as connection:
        connection.execute("DELETE FROM sms_check_runs WHERE request_key LIKE 'pytest-check-%'")


def headers(device):
    return {'Authorization':'Bearer '+device['token']}


def launch(gateway,target=TARGET,key=None):
    client,*_=gateway
    response=client.post('/business/v1/sms-checks',headers={**BUSINESS,'Idempotency-Key':key or 'pytest-check-'+uuid4().hex},
        json={'target_phone':target,'timeout_seconds':300})
    assert response.status_code==200,response.text
    return response.json()


def pull(gateway,device=None):
    client,sender,*_=gateway
    response=client.get('/mobile/v1/message',headers=headers(device or sender))
    assert response.status_code==200,response.text
    return response.json()


def report(gateway,item,state='Sent',device=None):
    client,sender,*_=gateway
    now=datetime.now(timezone.utc).isoformat()
    return client.patch('/mobile/v1/message',headers=headers(device or sender),json=[{
        'id':item['id'],'state':state,'states':{state:now},
        'recipients':[{'phoneNumber':item['phoneNumbers'][0],'state':state,'error':'No service' if state=='Failed' else None}]}])


def receive(gateway,item,sender=SOURCE,text=None,sim=1,device=None):
    client,_,receiver,*_=gateway
    response=client.post('/mobile/v1/inbox',headers=headers(device or receiver),json={
        'id':'inbox-check-'+uuid4().hex,'type':'SMS','sender':sender,'recipient':TARGET,'simNumber':sim,
        'receivedAt':datetime.now(timezone.utc).isoformat(),'textMessage':{'text':text or item['textMessage']['text']}})
    assert response.status_code==200,response.text
    assert response.json()['conversationId'] is None
    return response


def result(gateway,run_id,item):
    return next(r for r in gateway[3].results(run_id) if r['id']==item['id'])


def first_item(gateway):
    return next(item for item in pull(gateway) if item['simNumber']==1)


def test_fanout_sim_transport_and_no_chat_or_agent_notifications(gateway):
    run=launch(gateway)
    assert [r['status'] for r in run['results']].count('PENDING')==2
    assert [r['status'] for r in run['results']].count('NOT_TESTED')==1
    assert len(gateway[5].events)==2
    items=pull(gateway)
    assert {i['simNumber'] for i in items}=={1,2}
    assert pull(gateway)==[]  # Claimed once, no duplicate charge.
    item=next(i for i in items if i['simNumber']==1)
    assert report(gateway,item).status_code==200
    assert result(gateway,run['run_id'],item)['status']=='PENDING'
    receive(gateway,item,sender='6045550101')
    assert result(gateway,run['run_id'],item)['status']=='NORMAL'
    assert gateway[6].events==[]
    with psycopg.connect(gateway[4].dsn) as c:
        for table in ('messages','conversations','contacts','message_recipients','message_state_history'):
            assert c.execute('SELECT count(*) FROM '+table).fetchone()[0]==0


def test_receipt_before_send_and_duplicate_callbacks(gateway):
    run=launch(gateway)
    item=first_item(gateway)
    receive(gateway,item)
    assert result(gateway,run['run_id'],item)['status']=='PENDING'
    assert report(gateway,item).status_code==200
    assert report(gateway,item,'Processed').status_code==200
    receive(gateway,item)
    assert result(gateway,run['run_id'],item)['status']=='NORMAL'


@pytest.mark.parametrize('bad', ['sender','text','sim'])
def test_mismatches_are_problems_and_never_chat(gateway,bad):
    run=launch(gateway)
    item=first_item(gateway)
    assert report(gateway,item).status_code==200
    kwargs={'sender':SECOND} if bad=='sender' else {'text':item['textMessage']['text']+' changed'} if bad=='text' else {'sim':2}
    receive(gateway,item,**kwargs)
    assert result(gateway,run['run_id'],item)['status']=='PROBLEM'
    receive(gateway,item)
    assert result(gateway,run['run_id'],item)['status']=='PROBLEM'


def test_failure_stays_failed_even_if_receipt_arrives(gateway):
    run=launch(gateway)
    item=first_item(gateway)
    assert report(gateway,item,'Failed').status_code==200
    receive(gateway,item)
    row=result(gateway,run['run_id'],item)
    assert row['status']=='PROBLEM' and 'No service' in row['reason']


def test_expired_offline_and_sent_without_receipt(gateway):
    run=launch(gateway)
    with psycopg.connect(gateway[4].dsn) as c:
        c.execute('UPDATE sms_checks SET deadline_at=0 WHERE run_id=%s',(run['run_id'],))
    assert pull(gateway)==[]
    rows=gateway[3].results(run['run_id'])
    assert sum(r['status']=='PROBLEM' for r in rows)==2
    assert all('未拉取' in r['reason'] for r in rows if r['status']=='PROBLEM')
    run=launch(gateway)
    item=first_item(gateway)
    report(gateway,item)
    with psycopg.connect(gateway[4].dsn) as c:
        c.execute('UPDATE sms_checks SET deadline_at=0 WHERE id=%s',(item['id'],))
    receive(gateway,item)
    row=result(gateway,run['run_id'],item)
    assert row['status']=='PROBLEM' and '接收超时' in row['reason']


def test_external_number_and_manual_receipt(gateway):
    target='+16045550999'
    run=launch(gateway,target)
    item=first_item(gateway)
    assert item['phoneNumbers']==[target]
    report(gateway,item)
    response=gateway[0].post('/business/v1/sms-checks/'+run['run_id']+'/receipts',headers=BUSINESS,
        json={'sender':SOURCE,'text':item['textMessage']['text']})
    assert response.status_code==200,response.text
    row=result(gateway,run['run_id'],item)
    assert row['status']=='NORMAL' and row['receipt_source']=='MANUAL'


def test_replays_concurrency_and_authorization(gateway):
    client,sender,receiver,service,*_=gateway
    key='pytest-check-'+uuid4().hex
    run=launch(gateway,key=key)
    assert launch(gateway,key=key)['run_id']==run['run_id']
    response=client.post('/business/v1/sms-checks',headers={**BUSINESS,'Idempotency-Key':'pytest-check-'+uuid4().hex},json={'target_phone':TARGET})
    assert response.status_code==409
    assert client.get('/business/v1/sms-checks').status_code==401
    assert client.get('/business/v1/sms-checks',headers=headers(sender)).status_code==401
    transport=MessagePullService(service.database,service)
    with ThreadPoolExecutor(max_workers=2) as pool:
        batches=list(pool.map(lambda _:transport.pull(sender['id'],'fifo'),range(2)))
    assert sum(map(len,batches))==2
    item=next(i for batch in batches for i in batch).model_dump(by_alias=True)
    assert report(gateway,item,device=receiver).status_code==403
    receive(gateway,item,device=sender)
    assert result(gateway,run['run_id'],item)['status']=='PENDING'


def test_sim_changes_invalidate_results_and_block_sending(gateway):
    run=launch(gateway)
    with psycopg.connect(gateway[4].dsn) as c:
        c.execute('UPDATE sim_cards SET phone_number=%s WHERE device_id=%s AND sim_number=1',('+16045550777',gateway[1]['id']))
    items=pull(gateway)
    assert [i['simNumber'] for i in items]==[2]
    rows=gateway[3].results()
    changed=next(r for r in rows if r['source_phone']=='+16045550777')
    assert changed['status']=='NOT_TESTED'
    original=next(r for r in gateway[3].results(run['run_id']) if r['sim_card_id']==changed['sim_card_id'])
    assert original['status']=='PROBLEM'


def test_disabled_missing_numbers_and_never_tested(gateway):
    assert all(r['status']=='NOT_TESTED' for r in gateway[3].results())
    with psycopg.connect(gateway[4].dsn) as c:
        c.execute('UPDATE sim_cards SET enabled=FALSE WHERE device_id=%s AND sim_number=1',(gateway[1]['id'],))
        c.execute('UPDATE sim_cards SET phone_number=NULL WHERE device_id=%s AND sim_number=2',(gateway[1]['id'],))
    run=launch(gateway)
    assert len(run['results'])==1
    assert run['results'][0]['source_phone']==TARGET
    assert all(r['status']=='NOT_TESTED' for r in run['results'])
    assert pull(gateway)==[]


def test_invalid_manual_receipt_and_corrupted_diagnostics(gateway):
    run=launch(gateway)
    item=first_item(gateway)
    receive(gateway,item,text='VIRGO-CHECK:damaged')
    assert result(gateway,run['run_id'],item)['status']=='PENDING'
    response=gateway[0].post('/business/v1/sms-checks/'+run['run_id']+'/receipts',headers=BUSINESS,
        json={'sender':SOURCE,'text':'not a diagnostic'})
    assert response.status_code==400


def test_archived_devices_and_sims_excluded_without_deleting_history(gateway):
    with psycopg.connect(gateway[4].dsn) as c:
        c.execute("UPDATE sim_cards SET areas='Regina' WHERE device_id=%s",(gateway[2]['id'],))
        c.execute("UPDATE sim_cards SET areas='Edmonton' WHERE device_id=%s",(gateway[1]['id'],))
    run=launch(gateway)
    assert len(run['results'])==3
    assert {r['areas'] for r in run['results']}=={'Edmonton','Regina'}
    with psycopg.connect(gateway[4].dsn) as c:
        c.execute('UPDATE devices SET enabled=FALSE,unregistered_at=1 WHERE id=%s',(gateway[1]['id'],))
        c.execute('UPDATE sms_checks SET deadline_at=0 WHERE run_id=%s',(run['run_id'],))
    assert len(gateway[3].results())==1
    assert len(gateway[3].results(run['run_id']))==1
    new_run=launch(gateway)
    assert len(new_run['results'])==1
    with psycopg.connect(gateway[4].dsn) as c:
        assert c.execute('SELECT count(*) FROM sms_checks WHERE run_id=%s',(run['run_id'],)).fetchone()[0]==3
        c.execute('UPDATE sim_cards SET unregistered_at=1 WHERE device_id=%s',(gateway[2]['id'],))
    assert gateway[3].results()==[]
    assert launch(gateway)['results']==[]


def test_sim_history_tracks_replacement_and_delete_preserves_chat(gateway):
    from tests.integration.test_message_pull_service import seed_messages
    from pg.admin_service import PgAdminService
    device_id,sim_id,_,message_ids=seed_messages(gateway[4])
    admin=PgAdminService(Database(gateway[4].dsn))
    with psycopg.connect(gateway[4].dsn) as c:
        c.execute("UPDATE sim_cards SET phone_number='16045550701',areas='Regina' WHERE id=%s",(sim_id,))
        c.execute("UPDATE sim_cards SET phone_number='+1 (604) 555-0701' WHERE id=%s",(sim_id,))
        assert c.execute('SELECT count(*) FROM sim_card_history WHERE sim_card_id=%s',(sim_id,)).fetchone()[0]==0
        c.execute("UPDATE sim_cards SET phone_number='16045550702' WHERE id=%s",(sim_id,))
    history=[r for r in admin.list_sim_card_history() if r['sim_card_id']==sim_id]
    assert len(history)==1
    assert history[0]['replacement_phone']=='16045550702' and history[0]['areas']=='Regina'
    admin.delete_sim_card(sim_id)
    with psycopg.connect(gateway[4].dsn) as c:
        assert c.execute('SELECT 1 FROM sim_cards WHERE id=%s',(sim_id,)).fetchone() is None
        assert c.execute('SELECT sim_card_id FROM messages WHERE id=%s',(message_ids[0],)).fetchone()==(None,)
        assert c.execute('SELECT count(*) FROM sim_card_history WHERE sim_card_id=%s',(sim_id,)).fetchone()[0]==2
        assert c.execute('SELECT sim_card_id FROM conversations WHERE device_id=%s',(device_id,)).fetchone()==(None,)


def test_unregister_archives_then_removes_current_sim_cards(gateway):
    from pg.admin_service import PgAdminService
    device_id=gateway[1]['id']
    PgAdminService(Database(gateway[4].dsn)).unregister_device(device_id)
    with psycopg.connect(gateway[4].dsn) as c:
        assert c.execute('SELECT count(*) FROM sim_cards WHERE device_id=%s',(device_id,)).fetchone()[0]==0
        assert c.execute("SELECT count(*) FROM sim_card_history WHERE device_id=%s AND event_type='DELETED'",(device_id,)).fetchone()[0]==2


def test_admin_enabled_flag_survives_device_report_and_controls_availability(gateway):
    from pg.admin_service import PgAdminService, SimCardUpdate, AccountCreate
    from app.services.device_service import DeviceService
    from app.schemas.device import DeviceUpdateRequest
    database=Database(gateway[4].dsn)
    admin=PgAdminService(database)
    sim=next(row for row in admin.list_sim_card_options() if row['phone_number']==SOURCE)
    account_id='acc_enabled_'+uuid4().hex
    try:
        admin.create_account(AccountCreate(id=account_id,username=account_id,password='test-only',use_sims_ids=(sim['id'],)))
        admin.update_sim_card(sim['id'],SimCardUpdate(phone_number=SOURCE,enabled=False))
        DeviceService(database).update(gateway[1]['id'],DeviceUpdateRequest.model_validate({
            'id':gateway[1]['id'],'simCards':[{'slotIndex':0,'simNumber':1,'phoneNumber':SOURCE},
                {'slotIndex':1,'simNumber':2,'phoneNumber':SECOND}]}))
        with database.transaction() as c:
            assert c.execute('SELECT enabled FROM sim_cards WHERE id=%s',(sim['id'],)).fetchone()==(False,)
            assert c.execute('SELECT 1 FROM account_sim_cards WHERE account_id=%s',(account_id,)).fetchone() is None
            assert c.execute('SELECT use_sims_id FROM accounts WHERE id=%s',(account_id,)).fetchone()==(None,)
        assert sim['id'] not in {r['id'] for r in admin.list_sim_card_options()}
        assert sim['id'] not in {r['sim_card_id'] for r in gateway[3].results()}
        run=launch(gateway)
        assert sim['id'] not in {r['sim_card_id'] for r in run['results']}
        assert all(item['simNumber']!=1 for item in pull(gateway))
        admin.update_sim_card(sim['id'],SimCardUpdate(phone_number=SOURCE,enabled=True))
        assert sim['id'] in {r['id'] for r in admin.list_sim_card_options()}
        assert sim['id'] in {r['sim_card_id'] for r in gateway[3].results()}
    finally:
        admin.delete_account(account_id)
