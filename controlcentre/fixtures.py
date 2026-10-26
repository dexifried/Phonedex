"""Explicit synthetic development fixtures. No production data is imported."""
from datetime import datetime,timedelta,timezone
import random


def seed_demo(store):
    if store.get_meta('demo',False):return
    if any(store.list(k) for k in ('calls','contacts','providers','agents','tasks')) or store.get_meta('operator_password'):
        raise ValueError('A demo requires an empty data directory; existing workspace was preserved')
    store.set_meta('demo',True)
    agents=[dict(id='demo_concierge',name='Concierge',description='Appointments and everyday errands',mode='normal',
                 prompt='Identify yourself as an AI assistant. Ask clear questions and confirm details.',enabled=True,
                 max_duration_seconds=120,voice='Default',model='PhoneDex GPT Live',tools=[]),
            dict(id='demo_reception',name='Reception',description='A thoughtful first point of contact',mode='business',
                 prompt='Answer business questions using verified information.',enabled=True,
                 max_duration_seconds=120,voice='Default',model='PhoneDex GPT Live',tools=[])]
    for agent in agents:store.put('agents',agent)
    names=['Juniper Dental','Northline Auto','Cedar House','Harbour Pharmacy','Willow Studio','Atlas Support']
    for i,name in enumerate(names):
        store.put('contacts',dict(id=f'demo_contact_{i}',name=name,number=f'+120255501{10+i}',
            company=name,allowed=False,notes='Synthetic development fixture',tags=['Demo']))
    rng=random.Random(42)
    now=datetime.now(timezone.utc)
    for i in range(38):
        start=now-timedelta(hours=i*4.3+1)
        answered=i%9!=0
        duration=rng.randint(29,112) if answered else 0
        agent=agents[i%2]
        cid='call_'+f'{i+1:032x}'
        transcript=[]
        results=[]
        if answered:
            transcript=[dict(event_id=f'{cid}_a',speaker='agent',ms=500,at=start.isoformat(),text='Hello, I’m an AI assistant calling to confirm your business hours.'),
                        dict(event_id=f'{cid}_b',speaker='recipient',ms=4800,at=start.isoformat(),text='We’re open Monday to Friday, nine until five.'),
                        dict(event_id=f'{cid}_c',speaker='agent',ms=9000,at=start.isoformat(),text='Thank you. I’ll pass those confirmed hours along.')]
            results=[dict(label='Weekday hours',value='9:00 AM – 5:00 PM',provenance='confirmed',source_event_id=f'{cid}_b',source='Synthetic recipient transcript')]
        store.record_call(dict(id=cid,contact=names[i%6],number_masked=f'••• ••• 01{10+i%6}',
            direction='inbound' if i%4==0 else 'outbound',agent_id=agent['id'],agent_name=agent['name'],provider='Synthetic SIP',
            state='ended' if answered else 'no_answer',started_at=start.isoformat(),
            ended_at=(start+timedelta(seconds=duration)).isoformat(),duration_seconds=duration,
            outcome='completed' if answered else 'no_answer',task_success=i%5!=0 if answered else None,
            cost=round(duration/60*.02,4) if answered else 0,cost_kind='estimated',transcript=transcript,results=results,
            controls=[],timeline=[dict(at=start.isoformat(),state='dialing',message='Synthetic call started'),
                    dict(at=(start+timedelta(seconds=duration)).isoformat(),state='ended',message='Synthetic call ended')]))
    store.add_event(dict(kind='system',source='Control Centre',level='info',message='Synthetic workspace initialized. No real calls or charges.'))
