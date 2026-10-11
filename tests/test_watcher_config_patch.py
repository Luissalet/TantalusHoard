from tantalus_hoard.agent_tools import call_tool, tool_catalog


def test_partial_config_preserves_a_later_agent_change_and_manual_notes(svc):
    watcher = svc.store.create_watcher(name='Original', mode='availability', config={'product':{'terms':['old']},'policies':{'price_threshold':10,'seller':'retail_only'}}, enabled=False)
    svc.store.update_watcher(watcher['id'], name='Agent title', config={'product':{'terms':['agent']},'policies':{'price_threshold':10,'seller':'any'},'extra':'keep'})
    result = call_tool(svc, 'watcher_update', {'watcher_id':watcher['id'],'notes':'Manual notes','config_patch':{'policies':{'price_threshold':12}}})
    assert result['name'] == 'Agent title'
    assert result['notes'] == 'Manual notes'
    assert result['config'] == {'product':{'terms':['agent']},'policies':{'price_threshold':12,'seller':'any'},'extra':'keep'}


def test_partial_config_removes_only_explicit_keys(svc):
    watcher = svc.store.create_watcher(name='Original', mode='availability', config={'policies':{'msrp':20,'price_threshold':12},'product':{'terms':['keep']}},enabled=False)
    result = call_tool(svc, 'watcher_update', {'watcher_id':watcher['id'],'config_patch':{'policies':{'msrp':None}}})
    assert result['config'] == {'policies':{'price_threshold':12},'product':{'terms':['keep']}}


def test_full_replacement_still_works_and_the_native_schema_exposes_patch(svc):
    watcher = svc.store.create_watcher(name='Original', mode='availability', config={'old':True},enabled=False)
    result = call_tool(svc, 'watcher_update', {'watcher_id':watcher['id'],'config':{'new':True}})
    assert result['config'] == {'new':True}
    tool = next(tool for tool in tool_catalog() if tool['name'] == 'watcher_update')
    assert 'config_patch' in tool['inputSchema']['properties']
