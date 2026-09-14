from datetime import datetime
import secrets

from app.services.sms_check_service import LABELS


def build_sms_check_panel(ui, service):
    from nicegui import run

    ui.label('短信功能检测').classes('text-xl font-medium')
    ui.label('输入接收手机号，向所有可用基站 SIM 各发送一条检测短信。接收机接入 Virgo 时自动核对；否则请在下方粘贴实际收到的短信。')
    ui.label('正常 = 发送成功且发送号码、接收号码、短信内容核对通过。失败或超时标为需要检查，不能仅凭检测结果确定套餐过期。').classes('text-sm text-gray-600')
    ui.label('仅显示和检测当前已启用、有效且填写了手机号的 SIM。已注销、未启用及空号码不参与；本轮接收号自身保持未测试。').classes('text-sm text-gray-600')
    with ui.row().classes('items-end gap-3'):
        target = ui.input('接收手机号', placeholder='例如 +16045551234').props('outlined').classes('w-72')
        timeout = ui.number('等待时间（秒）', value=300,min=30,max=3600,precision=0).props('outlined').classes('w-48')
        start_button = ui.button('开始检测所有基站',icon='network_check')
    summary = ui.label('')
    skipped_summary = ui.label('').classes('text-sm text-gray-600')
    batch_label = ui.label('显示每张 SIM 的最近检测结果').classes('text-sm text-gray-600')
    state = {'run_id':None,'request_key':secrets.token_hex(16),'busy':False}
    columns = [{'name':key,'field':key,'label':label,'align':'left','sortable':True} for key,label in [
        ('status_label','检测状态'),('source_phone','发送手机号'),('areas','地区'),('device_name','设备'),('sim_number','SIM 编号'),
        ('target_phone','接收手机号'),('reason','检测说明'),('created_display','检测时间'),
        ('sent_display','发送时间'),('received_display','核对时间'),('receipt_label','核对方式')]]
    table = ui.table(columns=columns,rows=[],row_key='sim_card_id',pagination=30).classes('w-full')
    table.add_slot('body-cell-status_label','''<q-td :props="props"><q-badge
        :color="props.row.status === 'NORMAL' ? 'positive' : props.row.status === 'PROBLEM' ? 'negative' : props.row.status === 'PENDING' ? 'warning' : 'grey'">
        {{ props.value }}</q-badge></q-td>''')

    async def refresh():
        try:
            rows = await run.io_bound(service.results,state['run_id'])
            for row in rows:
                row['areas'] = row.get('areas') or '未分配'
                for key in ('created','sent','received'):
                    value = row.get(key+'_at')
                    row[key+'_display'] = datetime.fromtimestamp(value/1000).strftime('%Y-%m-%d %H:%M:%S') if value else ''
                row['receipt_label'] = {'MANUAL':'人工粘贴核对','DEVICE':'基站自动核对'}.get(row.get('receipt_source'),'')
            table.rows=rows
            table.update()
            summary.set_text(' / '.join(f'{label}：{sum(r["status"] == key for r in rows)}' for key,label in LABELS.items()))
            from collections import Counter
            reasons = Counter(row['reason'] for row in rows if row['status'] == 'NOT_TESTED')
            skipped_summary.set_text('未测试原因：' + '；'.join(f'{reason}（{count} 张）' for reason,count in reasons.items()) if reasons else '')
        except Exception:
            summary.set_text('检测结果读取失败，请稍后刷新')

    async def start():
        if state['busy']:
            return
        state['busy']=True
        start_button.disable()
        try:
            state['run_id'] = await run.io_bound(service.start,target.value or '',int(timeout.value or 300),state['request_key'])
            state['request_key']=secrets.token_hex(16)
            batch_label.set_text('当前检测批次：'+state['run_id'])
            ui.notify('检测已开始，正在等待发送和接收结果',type='positive')
            await refresh()
        except ValueError as error:
            ui.notify(str(error),type='warning')
        except Exception:
            ui.notify('发起检测失败，请刷新后重试',type='negative')
        finally:
            state['busy']=False
            start_button.enable()
    start_button.on_click(start)

    async def latest():
        state['run_id']=None
        batch_label.set_text('显示每张 SIM 的最近检测结果')
        await refresh()
    with ui.row():
        ui.button('刷新结果',on_click=refresh).props('flat')
        ui.button('查看所有 SIM 最近结果',on_click=latest).props('flat')
    with ui.expansion('人工核对接收短信（接收手机未接入 Virgo 时使用）',icon='sms').classes('w-full'):
        ui.label('仅粘贴你在指定接收手机上实际收到的发送号码和完整短信。系统会核对检测标识与号码；输入不匹配会标记为有问题。')
        sender = ui.input('短信显示的发送手机号').props('outlined').classes('w-72')
        body = ui.textarea('收到的完整短信内容').props('outlined').classes('w-full')
        async def record():
            try:
                # Token selects the original batch, including after a browser refresh.
                from app.services.sms_check_service import TOKEN
                match=TOKEN.search(body.value or '')
                row=next((r for r in table.rows if match and r.get('id') == 'check_'+match[1]),None)
                if not row:
                    raise ValueError('未找到对应检测，请确认短信属于当前显示的批次')
                await run.io_bound(service.record_receipt,row['run_id'],sender.value or '',body.value or '')
                body.set_value('')
                await refresh()
                ui.notify('已核对，请查看检测状态')
            except ValueError as error:
                ui.notify(str(error),type='warning')
            except Exception:
                ui.notify('核对失败，请稍后重试',type='negative')
        ui.button('提交实际接收内容',on_click=record)
    ui.timer(5,refresh)
    ui.timer(0.1,refresh,once=True)
