import { test } from 'node:test';
import assert from 'node:assert/strict';
import { changedFields, configPatch } from './src/copilot-context.js';

test('manual notes preserve simultaneous agent title and untouched settings', () => {
  assert.deepEqual(changedFields({name:'Old',notes:'Before'}, {name:'Old',notes:'Manual'}), {notes:'Manual'});
  const initial = { product:{terms:['old'],must:['native']}, policies:{seller:'retail',price_threshold:10}, custom:'keep' };
  const draft = {...initial,policies:{...initial.policies,price_threshold:12}};
  assert.deepEqual(configPatch(initial,draft), {policies:{price_threshold:12}});
});

test('explicit manual removal wins without removing newly added agent fields', () => {
  assert.deepEqual(configPatch({policies:{msrp:10,threshold:5}}, {policies:{threshold:5}}), {policies:{msrp:null}});
});

test('only changed target fields are sent, including manual clears', () => {
  assert.deepEqual(changedFields({label:'Before',msrp:'10',ean:'old'},
    {label:'Before',msrp:'',ean:'old'}), {msrp:''});
});
